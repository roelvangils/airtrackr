#!/usr/bin/env python3
"""
Shared database module for AirTrackr.

Provides a single get_connection() context manager used by all consumers,
with WAL mode, foreign keys, and schema migrations via PRAGMA user_version.
"""

import re
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

DB_PATH = Path("database/airtracker.db")

# Current schema version — bump this when adding migrations
SCHEMA_VERSION = 3


@contextmanager
def get_connection(db_path: Optional[Path] = None):
    """
    Context manager for database connections.

    Enables WAL mode for better concurrency (API reads while tracker writes),
    foreign keys, and row_factory for dict-like access.

    Args:
        db_path: Override database path (defaults to DB_PATH)

    Yields:
        sqlite3.Connection with row_factory set
    """
    path = db_path or DB_PATH
    path.parent.mkdir(exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def init_schema():
    """
    Initialize database schema and run any pending migrations.

    Uses PRAGMA user_version to track which migrations have been applied.
    """
    with get_connection() as conn:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        logger.info(f"Database schema version: {current_version}, target: {SCHEMA_VERSION}")

        if current_version < 1:
            _migrate_to_v1(conn)

        if current_version < 2:
            _migrate_to_v2(conn)

        if current_version < 3:
            _migrate_to_v3(conn)

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _migrate_to_v1(conn: sqlite3.Connection):
    """
    Migration to v1:
    - Create core tables if not exist
    - Add composite index (device_name, timestamp DESC)
    - Merge geocoding_cache into main database
    - Create zones table
    - Create location_summaries table
    """
    cursor = conn.cursor()

    # --- Core tables ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS swift_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            location TEXT,
            time_status TEXT,
            distance TEXT,
            latitude REAL,
            longitude REAL,
            device_type TEXT CHECK(device_type IN ('person', 'device', 'item')),
            raw_data TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            extracted_at TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS swift_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT UNIQUE NOT NULL,
            device_type TEXT CHECK(device_type IN ('person', 'device', 'item')),
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_location TEXT,
            update_count INTEGER DEFAULT 0
        )
    ''')

    # --- Indexes ---
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_swift_locations_device_name
        ON swift_locations(device_name)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_swift_locations_timestamp
        ON swift_locations(timestamp DESC)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_swift_locations_device_type
        ON swift_locations(device_type)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_swift_locations_extracted_at
        ON swift_locations(extracted_at)
    ''')

    # Composite index for the most common query pattern
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_swift_locations_device_timestamp
        ON swift_locations(device_name, timestamp DESC)
    ''')

    # --- Geocoding cache (merged from separate .db) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS geocoding_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_text TEXT UNIQUE NOT NULL,
            latitude REAL,
            longitude REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provider TEXT DEFAULT 'nominatim'
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_geocoding_cache_location_text
        ON geocoding_cache(location_text)
    ''')

    # Import existing geocoding cache data if the separate db exists
    _import_geocoding_cache(conn)

    # --- Zones table (for geofencing) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_meters REAL NOT NULL DEFAULT 100
        )
    ''')

    # --- Location summaries (for retention/aggregation) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS location_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            period_start TIMESTAMP NOT NULL,
            period_end TIMESTAMP NOT NULL,
            period_type TEXT NOT NULL CHECK(period_type IN ('hourly', 'daily')),
            predominant_location TEXT,
            latitude REAL,
            longitude REAL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            unique_locations INTEGER NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_location_summaries_device_period
        ON location_summaries(device_name, period_start DESC)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_location_summaries_type
        ON location_summaries(period_type)
    ''')

    conn.commit()
    logger.info("Migrated database to schema v1")


def _migrate_to_v2(conn: sqlite3.Connection):
    """
    Migration to v2:
    - Add location_timestamp column to swift_locations (computed from relative time)
    - Create location_aliases table (Home → real address for geocoding)
    """
    cursor = conn.cursor()

    # Add location_timestamp column (nullable — old records won't have it)
    try:
        cursor.execute('''
            ALTER TABLE swift_locations ADD COLUMN location_timestamp TIMESTAMP
        ''')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Location aliases: map Find My names to real addresses for geocoding
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS location_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias TEXT UNIQUE NOT NULL,
            address TEXT NOT NULL
        )
    ''')

    # Seed default aliases
    cursor.executemany(
        'INSERT OR IGNORE INTO location_aliases (alias, address) VALUES (?, ?)',
        [
            ('Home', 'Onderstraat 7, 9000 Ghent'),
            ('Work', 'Kouter 7, 9000 Ghent'),
        ],
    )

    conn.commit()
    logger.info("Migrated database to schema v2")


def _migrate_to_v3(conn: sqlite3.Connection):
    """
    Migration to v3:
    - Structured address columns on geocoding_cache
    - distance_from_home_km and battery_status on swift_locations
    - trips table for movement tracking
    - visits table for dwell-time tracking
    """
    cursor = conn.cursor()

    # --- Structured address fields on geocoding_cache ---
    for col, col_type in [
        ('street', 'TEXT'),
        ('house_number', 'TEXT'),
        ('postal_code', 'TEXT'),
        ('city', 'TEXT'),
        ('country', 'TEXT'),
        ('address_json', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE geocoding_cache ADD COLUMN {col} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists

    # --- Enrichment columns on swift_locations ---
    for col, col_type in [
        ('distance_from_home_km', 'REAL'),
        ('battery_status', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE swift_locations ADD COLUMN {col} {col_type}')
        except sqlite3.OperationalError:
            pass

    # --- Trips table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP NOT NULL,
            start_location TEXT,
            end_location TEXT,
            start_lat REAL,
            start_lon REAL,
            end_lat REAL,
            end_lon REAL,
            distance_km REAL,
            duration_minutes REAL
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_trips_device_time
        ON trips(device_name, start_time DESC)
    ''')

    # --- Visits table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            location TEXT,
            latitude REAL,
            longitude REAL,
            arrival_time TIMESTAMP NOT NULL,
            departure_time TIMESTAMP,
            duration_minutes REAL
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_visits_device_time
        ON visits(device_name, arrival_time DESC)
    ''')

    conn.commit()
    logger.info("Migrated database to schema v3")


def _import_geocoding_cache(conn: sqlite3.Connection):
    """Import data from the separate geocoding_cache.db if it exists."""
    cache_db_path = Path("database/geocoding_cache.db")
    if not cache_db_path.exists():
        return

    try:
        cache_conn = sqlite3.connect(str(cache_db_path))
        cache_cursor = cache_conn.cursor()
        cache_cursor.execute(
            "SELECT location_text, latitude, longitude, created_at, provider FROM geocoding_cache"
        )
        rows = cache_cursor.fetchall()
        cache_conn.close()

        if rows:
            conn.executemany(
                '''
                INSERT OR IGNORE INTO geocoding_cache
                (location_text, latitude, longitude, created_at, provider)
                VALUES (?, ?, ?, ?, ?)
                ''',
                rows,
            )
            conn.commit()
            logger.info(f"Imported {len(rows)} geocoding cache entries from separate database")

    except Exception as e:
        logger.warning(f"Could not import geocoding cache: {e}")


def resolve_location_alias(location: str) -> str:
    """
    Resolve a location alias (like "Home") to its real address.

    Returns the original location if no alias is found.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                'SELECT address FROM location_aliases WHERE alias = ? COLLATE NOCASE',
                (location,),
            ).fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return location


# Regex patterns for time status strings that may leak into location text.
# Matches: "6 min ago", "2 hours ago", "8 mo ago", "3 days ago", "Last mo",
# "Last week", "Yesterday", "Now", "Paused"
_TIME_PATTERNS = [
    r'\d+\s+min\s+ago',
    r'\d+\s+(?:hr|hours?)\s+ago',
    r'\d+\s+days?\s+ago',
    r'\d+\s+mo\s+ago',
    r'\d+\s+weeks?\s+ago',
    r'Last\s+\w+',
    r'Yesterday',
    r'Now',
    r'Paused',
]
_TIME_SUFFIX_RE = re.compile(
    r',\s*(' + '|'.join(_TIME_PATTERNS) + r')\s*$',
    re.IGNORECASE,
)
_TIME_VALUE_RE = re.compile(
    r'^(' + '|'.join(_TIME_PATTERNS) + r')$',
    re.IGNORECASE,
)
_DISTANCE_NUM_RE = re.compile(r'^\d+\s+(km|m)$')

# Patterns for converting relative time to absolute timestamps.
# Uses relativedelta for months so "10 mo ago" on Feb 5 gives Apr 5, not a 300-day guess.
_RELATIVE_TIME_RULES_TD = [
    (re.compile(r'^(\d+)\s+min\s+ago$', re.I), lambda m: timedelta(minutes=int(m.group(1)))),
    (re.compile(r'^(\d+)\s+(?:hr|hours?)\s+ago$', re.I), lambda m: timedelta(hours=int(m.group(1)))),
    (re.compile(r'^(\d+)\s+days?\s+ago$', re.I), lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r'^(\d+)\s+weeks?\s+ago$', re.I), lambda m: timedelta(weeks=int(m.group(1)))),
    (re.compile(r'^Yesterday$', re.I), lambda m: timedelta(days=1)),
    (re.compile(r'^Last\s+week$', re.I), lambda m: timedelta(weeks=1)),
    (re.compile(r'^Now$', re.I), lambda m: timedelta(seconds=0)),
]
_RELATIVE_TIME_RULES_RD = [
    (re.compile(r'^(\d+)\s+mo\s+ago$', re.I), lambda m: relativedelta(months=int(m.group(1)))),
    (re.compile(r'^Last\s+mo$', re.I), lambda m: relativedelta(months=1)),
]


def _time_status_to_timestamp(time_status: str, base_time: Optional[datetime] = None) -> Optional[str]:
    """
    Convert a relative time status like "15 min ago" to an ISO timestamp.

    Uses calendar-aware month arithmetic: "10 mo ago" on 2026-02-05 → 2025-04-05.

    Args:
        time_status: Relative time string (e.g. "15 min ago", "Now", "Paused")
        base_time: Reference time to compute from (defaults to now).
                   Use the record's timestamp when backfilling historical data.

    Returns:
        ISO timestamp string, or None if the pattern is not recognized (e.g. "Paused").
    """
    now = base_time or datetime.now()
    for pattern, delta_fn in _RELATIVE_TIME_RULES_TD:
        m = pattern.match(time_status)
        if m:
            return (now - delta_fn(m)).strftime('%Y-%m-%d %H:%M:%S')
    for pattern, delta_fn in _RELATIVE_TIME_RULES_RD:
        m = pattern.match(time_status)
        if m:
            return (now - delta_fn(m)).strftime('%Y-%m-%d %H:%M:%S')
    return None


def sanitize_device_data(device_data: Dict) -> Optional[Dict]:
    """
    Clean up parsed device data from the Swift extractor.

    Fixes known issues in parseDeviceInfo():
    1. Decimal distances (e.g. "0,8 km") are split on the comma, leaving
       timeStatus="0" and distance="8 km" instead of the real values.
    2. The real time status ("8 mo ago") gets appended to the location text.
    3. "No location found" entries are noise and should be skipped.
    4. When there's no distance in the raw data, the parser puts the time
       status in the distance field and a city name in the timeStatus field.
    5. Relative time statuses ("15 min ago") are converted to absolute timestamps
       and stored in the location_timestamp field.

    Args:
        device_data: Dict with keys name, location, timeStatus, distance, rawText

    Returns:
        Cleaned dict, or None if the record should be skipped entirely.
    """
    location = device_data.get('location', '')
    time_status = device_data.get('timeStatus', '')
    distance = device_data.get('distance', '')

    # Skip "No location found" — these are noise
    if location == 'No location found' or location == 'Unknown':
        return None

    # Bug 1: Decimal-distance parsing bug.
    # When timeStatus is a bare number (e.g. "0") and distance looks like "8 km",
    # the actual distance was "0,8 km" and the real time status is hiding at the
    # end of the location string.
    if time_status.isdigit() and _DISTANCE_NUM_RE.match(distance):
        actual_distance = f"{time_status},{distance}"

        # Try to extract the real time status from the location tail
        match = _TIME_SUFFIX_RE.search(location)
        if match:
            actual_time_status = match.group(1)
            actual_location = location[:match.start()].rstrip(', ')
            device_data['location'] = actual_location
            device_data['timeStatus'] = actual_time_status
            device_data['distance'] = actual_distance
        else:
            # Can't find time in location — just fix the distance
            device_data['distance'] = actual_distance
            device_data['timeStatus'] = 'Unknown'

    # Bug 2: No-distance case.
    # When Find My doesn't show distance, the parser puts the time in the
    # distance field and a location part (city) in the timeStatus field.
    # e.g. timeStatus="Ghent", distance="15 min ago"
    elif _TIME_VALUE_RE.match(distance) and not _TIME_VALUE_RE.match(time_status):
        # Reassemble location to include the misplaced city name
        if location:
            device_data['location'] = f"{location}, {time_status}"
        else:
            device_data['location'] = time_status
        device_data['timeStatus'] = distance
        device_data['distance'] = '-'

    # Convert relative time status to absolute timestamp
    location_timestamp = _time_status_to_timestamp(device_data.get('timeStatus', ''))
    if location_timestamp:
        device_data['location_timestamp'] = location_timestamp

    # Final cleanup: strip stray trailing commas/spaces from location
    device_data['location'] = device_data['location'].strip(', ')

    # Don't store empty locations
    if not device_data['location']:
        return None

    return device_data


def is_duplicate(
    conn: sqlite3.Connection,
    device_name: str,
    location: str,
    window_minutes: int = 2,
) -> bool:
    """
    Check if an identical location record exists within the given time window.

    Args:
        conn: Active database connection
        device_name: Device name to check
        location: Location text to check
        window_minutes: Time window in minutes (default 2)

    Returns:
        True if a duplicate exists within the window
    """
    cutoff = (datetime.now() - timedelta(minutes=window_minutes)).isoformat()
    cursor = conn.execute(
        '''
        SELECT 1 FROM swift_locations
        WHERE device_name = ? AND location = ? AND timestamp > ?
        LIMIT 1
        ''',
        (device_name, location, cutoff),
    )
    return cursor.fetchone() is not None
