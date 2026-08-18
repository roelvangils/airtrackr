#!/usr/bin/env python3
"""
Shared database module for AirTrackr.

Provides a single get_connection() context manager used by all consumers,
with WAL mode, foreign keys, and schema migrations via PRAGMA user_version.
"""

import os
import re
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

# Anchored to this file, not the process CWD — the API and the tracker are launched
# from different working directories (and from launchd, which has none to speak of),
# and a relative path silently created a second, empty database.
DB_PATH = Path(os.environ.get("AIRTRACKR_DB", Path(__file__).resolve().parent / "database" / "airtracker.db"))

# Current schema version — bump this when adding migrations
SCHEMA_VERSION = 6


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
    conn.execute("PRAGMA busy_timeout=15000")
    # Bound the WAL: on a machine that runs unattended for months, an unchecked WAL
    # is disk growth nobody notices until it hurts. Checkpoints trim it to this.
    conn.execute("PRAGMA journal_size_limit=8388608")
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

        if current_version < 4:
            _migrate_to_v4(conn)

        if current_version < 5:
            _migrate_to_v5(conn)

        if current_version < 6:
            _migrate_to_v6(conn)

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
            device_type TEXT CHECK(device_type IN ('person', 'device', 'item', 'me')),
            raw_data TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            extracted_at TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS swift_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT UNIQUE NOT NULL,
            device_type TEXT CHECK(device_type IN ('person', 'device', 'item', 'me')),
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


def _migrate_to_v4(conn: sqlite3.Connection):
    """
    Migration to v4:
    - Drop contacts and contact_addresses tables if they exist
    - Clean up any 'contact' device_type records
    """
    cursor = conn.cursor()

    # Drop contacts tables
    cursor.execute('DROP TABLE IF EXISTS contact_addresses')
    cursor.execute('DROP TABLE IF EXISTS contacts')

    # Remove any contact-type records from device tables
    cursor.execute("DELETE FROM swift_locations WHERE device_type = 'contact'")
    cursor.execute("DELETE FROM swift_devices WHERE device_type = 'contact'")

    conn.commit()
    logger.info("Migrated database to schema v4")


_OLD_DEVICE_TYPE_CHECK = "device_type TEXT CHECK(device_type IN ('person', 'device', 'item'))"
_NEW_DEVICE_TYPE_CHECK = "device_type TEXT CHECK(device_type IN ('person', 'device', 'item', 'me'))"


def _relax_device_type_check(conn: sqlite3.Connection, table: str):
    """
    Add 'me' to a table's device_type CHECK constraint.

    SQLite cannot alter a CHECK in place, so the table is rebuilt: capture the
    original CREATE statement, rewrite just the CHECK clause, copy the rows over,
    and restore the indexes (dropping a table drops its indexes with it).
    """
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        return  # table doesn't exist yet; _migrate_to_v1 will create it with the new CHECK

    create_sql = row[0]
    if "'me'" in create_sql:
        return  # already relaxed
    if _OLD_DEVICE_TYPE_CHECK not in create_sql:
        logger.warning("Unexpected device_type CHECK on %s; leaving it alone", table)
        return

    index_sql = [
        r[0] for r in cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        ).fetchall()
    ]
    columns = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
    column_list = ", ".join(f'"{c}"' for c in columns)

    cursor.execute(f"ALTER TABLE {table} RENAME TO {table}_pre_v5")
    cursor.execute(create_sql.replace(_OLD_DEVICE_TYPE_CHECK, _NEW_DEVICE_TYPE_CHECK))
    cursor.execute(f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM {table}_pre_v5")
    cursor.execute(f"DROP TABLE {table}_pre_v5")
    for sql in index_sql:
        cursor.execute(sql)

    logger.info("Relaxed device_type CHECK on %s to include 'me'", table)


def _migrate_to_v5(conn: sqlite3.Connection):
    """
    Migration to v5 (macOS 27 / Find My 5.0 extractor):
    - Allow device_type 'me' for the new fourth Find My tab
    - Structured fields the rewritten Swift extractor now reports directly:
      distance_km, proximity, has_location
    """
    cursor = conn.cursor()

    for col, col_type in [
        ('distance_km', 'REAL'),
        ('proximity', 'TEXT'),
        ('has_location', 'INTEGER'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE swift_locations ADD COLUMN {col} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists

    for table in ('swift_locations', 'swift_devices'):
        _relax_device_type_check(conn, table)

    conn.commit()
    logger.info("Migrated database to schema v5")


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


# Find My on macOS 26+ abbreviates units with a trailing period — "14 min. ago",
# "2 hr. ago" — where it used to write "14 min ago". Every pattern below therefore
# allows an optional '.' after the unit; without it each relative timestamp silently
# resolved to None.
_UNIT_DOT = r'\.?'

# Stale time statuses — hours, days, weeks, months old = no real location update
_STALE_TIME_RE = re.compile(
    r'^(\d+)\s+(hr|hrs|hours?|days?|weeks?|mo|months?)' + _UNIT_DOT + r'\s+ago$'
    r'|^Yesterday$|^Last\s+(week|mo)$',
    re.IGNORECASE,
)

# Patterns for converting relative time to absolute timestamps.
# Uses relativedelta for months so "10 mo ago" on Feb 5 gives Apr 5, not a 300-day guess.
_RELATIVE_TIME_RULES_TD = [
    (re.compile(r'^(\d+)\s+(?:min|mins|minutes?)' + _UNIT_DOT + r'\s+ago$', re.I),
     lambda m: timedelta(minutes=int(m.group(1)))),
    (re.compile(r'^(\d+)\s+(?:hr|hrs|hours?)' + _UNIT_DOT + r'\s+ago$', re.I),
     lambda m: timedelta(hours=int(m.group(1)))),
    (re.compile(r'^(\d+)\s+days?' + _UNIT_DOT + r'\s+ago$', re.I),
     lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r'^(\d+)\s+weeks?' + _UNIT_DOT + r'\s+ago$', re.I),
     lambda m: timedelta(weeks=int(m.group(1)))),
    (re.compile(r'^Yesterday$', re.I), lambda m: timedelta(days=1)),
    (re.compile(r'^Last\s+week$', re.I), lambda m: timedelta(weeks=1)),
    (re.compile(r'^(?:Now|Just now)$', re.I), lambda m: timedelta(seconds=0)),
]
_RELATIVE_TIME_RULES_RD = [
    (re.compile(r'^(\d+)\s+(?:mo|months?)' + _UNIT_DOT + r'\s+ago$', re.I),
     lambda m: relativedelta(months=int(m.group(1)))),
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
    # UTC, like every other timestamp in this database (see _migrate_to_v6). Mixing
    # local time in here is what broke the duplicate check for months.
    now = base_time or datetime.now(timezone.utc)
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
    Validate a device row from the Swift extractor and decide whether to store it.

    This used to repair the extractor's output as well: the old parser split one
    comma-joined accessibility string, so a decimal distance like "0,8 km" tore in
    half and the real time status ended up glued to the location. The macOS 26+
    extractor reads each field from its own accessibility element, so there is
    nothing left to repair — only to validate.

    Rules:
    1. Rows with no location are skipped (has_location false, or empty location).
    2. "Paused" means updates are suspended, but the last known location is still
       good — only skip when there is no location to go with it.
    3. Stale rows (hours/days/weeks/months old) are not real location updates.
    4. Relative time ("15 min. ago") becomes an absolute location_timestamp.

    Args:
        device_data: Row dict from the extractor — name, location, time_status,
                     distance, proximity, has_location, battery.

    Returns:
        The dict, possibly with location_timestamp added, or None to skip the row.
    """
    location = (device_data.get('location') or '').strip(', ')
    time_status = device_data.get('time_status') or ''

    # The extractor tells us outright when a row has no location ("No location
    # found", or a bare "Paused" with nothing else).
    if device_data.get('has_location') is False:
        return None

    if not location or location in ('No location found', 'Unknown'):
        return None

    if time_status == 'Paused' and not location:
        return None

    # Skip stale records — old cached data, not a real location update
    if _STALE_TIME_RE.match(time_status):
        return None

    location_timestamp = _time_status_to_timestamp(time_status)
    if location_timestamp:
        device_data['location_timestamp'] = location_timestamp

    device_data['location'] = location
    return device_data


def resolve_device_alias(device_name: str) -> str:
    """
    Resolve a device alias (like a phone number) to its canonical name.

    Find My sometimes shows phone numbers instead of contact names.
    The device_aliases table maps these to the correct name.

    Returns the original name if no alias is found.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                'SELECT canonical_name FROM device_aliases WHERE alias = ?',
                (device_name,),
            ).fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return device_name


def _migrate_to_v6(conn: sqlite3.Connection):
    """
    Migration to v6: every timestamp in the database is UTC, without exception.

    Until v5 the columns disagreed: `timestamp` and `last_seen` were UTC (SQLite's
    CURRENT_TIMESTAMP), while `extracted_at` and `location_timestamp` were written in
    local time. That split produced a family of latent comparison bugs — the duplicate
    check and the retention cutoffs compared local `isoformat()` strings (with a "T")
    against stored UTC strings (with a space), and since "T" sorts after " ", the
    comparisons were wrong in both timezone and format. The dedup never suppressed a
    row because of it.

    Naive local values are converted here; the API attaches an explicit UTC offset on
    the way out, so consumers (dashboard, kortex) convert to local time for display.
    """
    logger.info("Migrating to v6: converting local-time columns to UTC")
    conn.execute("""
        UPDATE swift_locations
        SET extracted_at = datetime(extracted_at, 'utc')
        WHERE extracted_at IS NOT NULL AND extracted_at != ''
    """)
    conn.execute("""
        UPDATE swift_locations
        SET location_timestamp = datetime(location_timestamp, 'utc')
        WHERE location_timestamp IS NOT NULL AND location_timestamp != ''
    """)


def is_duplicate(
    conn: sqlite3.Connection,
    device_name: str,
    location: str,
    heartbeat_minutes: int = 60,
    coarse_fallback: Optional[str] = None,
) -> bool:
    """
    Skip saving if the device is still at the same location.

    Only saves a new record when:
    - The location has CHANGED from the last known location, OR
    - At least heartbeat_minutes have passed (hourly heartbeat to confirm presence)

    This comparison is what makes the table a true "last known location" history:
    a parked car is one row plus hourly heartbeats, no matter how often the tracker
    looks. The check runs against the device's most recent row regardless of its
    location, so A -> B -> A within the heartbeat window records all three moves.

    coarse_fallback handles a wrinkle introduced by street-level addresses: rows
    normally store the street ("Kortrijksesteenweg, Ghent"), but when the details
    sweep misses a row it degrades to the plain list label ("Ghent"). That is the
    same physical location, not a move — without this, every sweep miss would write
    a spurious "moved to Ghent" row and a "moved back" row the cycle after. Pass the
    plain label when (and only when) the new row has no street address; the last
    row's own plain label lives in its raw_data.

    Args:
        conn: Active database connection
        device_name: Device name to check
        location: Location text to check
        heartbeat_minutes: Max time between records at same location (default 60)
        coarse_fallback: The plain list label, when the new row lacks a street address

    Returns:
        True if the record should be skipped (duplicate)
    """
    row = conn.execute(
        '''
        SELECT location, timestamp,
               json_extract(raw_data, '$.location') AS coarse
        FROM swift_locations
        WHERE device_name = ?
        ORDER BY timestamp DESC
        LIMIT 1
        ''',
        (device_name,),
    ).fetchone()

    if row is None:
        return False  # First record for this device — always save

    last_location, last_timestamp, last_coarse = row

    same_place = last_location == location
    if not same_place and coarse_fallback is not None:
        # New row is street-less; same underlying list label counts as same place.
        same_place = last_coarse == coarse_fallback

    # Location changed — always save
    if not same_place:
        return False

    # Same location — only save if heartbeat interval has passed.
    #
    # The comparison is a string comparison against the stored format, and the rows
    # store UTC with a space separator ("2026-08-18 13:24:17"). This used to compare
    # against local time in isoformat ("2026-08-18T15:24:17"): the timezone offset was
    # wrong AND "T" sorts after " ", so every stored timestamp compared as older than
    # the cutoff — the dedup never suppressed anything and the table grew one row per
    # device per cycle regardless of movement.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=heartbeat_minutes)) \
        .strftime('%Y-%m-%d %H:%M:%S')
    return last_timestamp > cutoff
