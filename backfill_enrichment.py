#!/usr/bin/env python3
"""
Retroactive data enrichment backfill for AirTrackr.

Enriches existing records that were created before the v3 schema:
1. Compute location_timestamp for old records (NULL values)
2. Compute distance_from_home_km for records with coordinates
3. Reverse geocode unique coordinate pairs for structured addresses
4. Detect historical trips and visits from existing location data
5. Re-sanitize old dirty records

Usage:
    python backfill_enrichment.py [--dry-run] [--step STEP]

Steps: timestamps, distances, addresses, trips, visits, sanitize, all (default)
"""

import argparse
import json
import logging
from datetime import datetime

from db import get_connection, init_schema, sanitize_device_data, _time_status_to_timestamp
from enrichment import compute_distance_from_home, haversine_km, detect_trips, update_visits, TRIP_THRESHOLD_KM
from geocoding import Geocoder

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def backfill_location_timestamps(dry_run: bool = False) -> int:
    """
    Compute location_timestamp for records where it is NULL but time_status is available.

    Returns:
        Number of records updated
    """
    logger.info("Backfilling location_timestamp from time_status...")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, time_status, timestamp
            FROM swift_locations
            WHERE location_timestamp IS NULL AND time_status IS NOT NULL AND time_status != ''
        """)
        rows = cursor.fetchall()

        if not rows:
            logger.info("No records need location_timestamp backfill")
            return 0

        updated = 0
        for row in rows:
            # Use the record's own timestamp as base, not now()
            try:
                base_time = datetime.fromisoformat(row['timestamp'])
            except (ValueError, TypeError):
                base_time = None
            ts = _time_status_to_timestamp(row['time_status'], base_time=base_time)
            if ts:
                if not dry_run:
                    cursor.execute(
                        "UPDATE swift_locations SET location_timestamp = ? WHERE id = ?",
                        (ts, row['id']),
                    )
                updated += 1

        if not dry_run:
            conn.commit()

        action = "Would update" if dry_run else "Updated"
        logger.info(f"{action} {updated}/{len(rows)} records with location_timestamp")
        return updated


def backfill_distance_from_home(dry_run: bool = False) -> int:
    """
    Compute distance_from_home_km for records that have coordinates but no distance.

    Returns:
        Number of records updated
    """
    logger.info("Backfilling distance_from_home_km...")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, latitude, longitude
            FROM swift_locations
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND distance_from_home_km IS NULL
        """)
        rows = cursor.fetchall()

        if not rows:
            logger.info("No records need distance_from_home backfill")
            return 0

        updated = 0
        for row in rows:
            dist = compute_distance_from_home(row['latitude'], row['longitude'])
            if dist is not None:
                if not dry_run:
                    cursor.execute(
                        "UPDATE swift_locations SET distance_from_home_km = ? WHERE id = ?",
                        (dist, row['id']),
                    )
                updated += 1

        if not dry_run:
            conn.commit()

        action = "Would update" if dry_run else "Updated"
        logger.info(f"{action} {updated}/{len(rows)} records with distance_from_home_km")
        return updated


def backfill_structured_addresses(dry_run: bool = False) -> int:
    """
    Reverse geocode unique coordinate pairs to fill in structured address fields.

    Returns:
        Number of geocoding_cache records enriched
    """
    logger.info("Backfilling structured addresses via reverse geocoding...")

    geocoder = Geocoder()

    with get_connection() as conn:
        cursor = conn.cursor()

        # Find cache entries with coordinates but no structured data
        cursor.execute("""
            SELECT id, location_text, latitude, longitude
            FROM geocoding_cache
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND city IS NULL
        """)
        rows = cursor.fetchall()

        if not rows:
            logger.info("No geocoding_cache records need structured address backfill")
            return 0

        updated = 0
        for row in rows:
            if dry_run:
                logger.info(f"[DRY RUN] Would reverse geocode ({row['latitude']}, {row['longitude']}) for '{row['location_text']}'")
                updated += 1
                continue

            result = geocoder.reverse_geocode(row['latitude'], row['longitude'])
            if result:
                cursor.execute("""
                    UPDATE geocoding_cache
                    SET street = ?, house_number = ?, postal_code = ?, city = ?, country = ?,
                        address_json = ?
                    WHERE id = ?
                """, (
                    result.get('street'),
                    result.get('house_number'),
                    result.get('postal_code'),
                    result.get('city'),
                    result.get('country'),
                    json.dumps(result),
                    row['id'],
                ))
                updated += 1
                logger.info(f"Enriched '{row['location_text']}' -> {result.get('city')}, {result.get('street')}")

        if not dry_run:
            conn.commit()

        action = "Would enrich" if dry_run else "Enriched"
        logger.info(f"{action} {updated}/{len(rows)} geocoding_cache records")
        return updated


def backfill_trips(dry_run: bool = False) -> int:
    """
    Detect historical trips from all existing location data.

    Returns:
        Number of trips detected
    """
    logger.info("Backfilling trip detection...")

    if dry_run:
        logger.info("[DRY RUN] Would detect trips from all historical data")
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()

        # Clear existing trips to rebuild from scratch
        cursor.execute("DELETE FROM trips")
        conn.commit()

        # Get all devices with coordinate data
        cursor.execute("""
            SELECT DISTINCT device_name FROM swift_locations
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """)
        devices = [row[0] for row in cursor.fetchall()]

    total_trips = 0
    for device_name in devices:
        # Use a very large window to capture all historical data
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, location, latitude, longitude, timestamp
                FROM swift_locations
                WHERE device_name = ?
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY timestamp ASC
            """, (device_name,))
            rows = cursor.fetchall()

            if len(rows) < 2:
                continue

            trips = 0
            for i in range(1, len(rows)):
                prev = rows[i - 1]
                curr = rows[i]

                dist = haversine_km(
                    prev['latitude'], prev['longitude'],
                    curr['latitude'], curr['longitude'],
                )

                if dist >= TRIP_THRESHOLD_KM:
                    try:
                        t_start = datetime.fromisoformat(prev['timestamp'])
                        t_end = datetime.fromisoformat(curr['timestamp'])
                        duration = (t_end - t_start).total_seconds() / 60.0
                    except (ValueError, TypeError):
                        duration = None

                    cursor.execute("""
                        INSERT INTO trips
                        (device_name, start_time, end_time, start_location, end_location,
                         start_lat, start_lon, end_lat, end_lon, distance_km, duration_minutes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        device_name,
                        prev['timestamp'],
                        curr['timestamp'],
                        prev['location'],
                        curr['location'],
                        prev['latitude'],
                        prev['longitude'],
                        curr['latitude'],
                        curr['longitude'],
                        round(dist, 3),
                        round(duration, 1) if duration is not None else None,
                    ))
                    trips += 1

            conn.commit()
            total_trips += trips

        if trips:
            logger.info(f"Detected {trips} trip(s) for {device_name}")

    logger.info(f"Total trips detected: {total_trips}")
    return total_trips


def backfill_visits(dry_run: bool = False) -> int:
    """
    Reconstruct visit history from all existing location data.

    Returns:
        Number of visits created
    """
    logger.info("Backfilling visit detection...")

    if dry_run:
        logger.info("[DRY RUN] Would reconstruct visits from all historical data")
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()

        # Clear existing visits to rebuild from scratch
        cursor.execute("DELETE FROM visits")
        conn.commit()

        # Get all devices
        cursor.execute("SELECT DISTINCT device_name FROM swift_locations")
        devices = [row[0] for row in cursor.fetchall()]

    total_visits = 0
    for device_name in devices:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT location, latitude, longitude, timestamp
                FROM swift_locations
                WHERE device_name = ?
                ORDER BY timestamp ASC
            """, (device_name,))
            rows = cursor.fetchall()

        for row in rows:
            try:
                update_visits(
                    device_name,
                    row['location'],
                    row['latitude'],
                    row['longitude'],
                    row['timestamp'],
                )
            except Exception as e:
                logger.debug(f"Visit update failed for {device_name}: {e}")

        # Count visits created for this device
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM visits WHERE device_name = ?", (device_name,))
            count = cursor.fetchone()[0]
            total_visits += count

        if count:
            logger.info(f"Created {count} visit(s) for {device_name}")

    logger.info(f"Total visits created: {total_visits}")
    return total_visits


def backfill_resanitize(dry_run: bool = False) -> int:
    """
    Re-sanitize old records that may have bad data from pre-sanitization era.

    Returns:
        Number of records cleaned
    """
    logger.info("Re-sanitizing old records...")

    with get_connection() as conn:
        cursor = conn.cursor()

        # Find records with suspicious patterns (bare-digit time_status, time in distance, etc.)
        cursor.execute("""
            SELECT id, device_name, location, time_status, distance, raw_data
            FROM swift_locations
            WHERE (
                (time_status GLOB '[0-9]' AND distance GLOB '[0-9]* km')
                OR (distance GLOB '[0-9]* min ago' OR distance GLOB '[0-9]* hr ago'
                    OR distance GLOB '[0-9]* mo ago' OR distance = 'Now'
                    OR distance = 'Yesterday' OR distance GLOB 'Last *')
            )
        """)
        rows = cursor.fetchall()

        if not rows:
            logger.info("No records need re-sanitization")
            return 0

        updated = 0
        for row in rows:
            # Try to reconstruct original data from raw_data
            raw = row['raw_data']
            if not raw:
                continue

            try:
                original = json.loads(raw)
            except Exception:
                continue

            cleaned = sanitize_device_data(dict(original))
            if cleaned is None:
                continue

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would fix {row['device_name']}: "
                    f"'{row['location']}' -> '{cleaned['location']}', "
                    f"time '{row['time_status']}' -> '{cleaned['timeStatus']}'"
                )
                updated += 1
                continue

            cursor.execute("""
                UPDATE swift_locations
                SET location = ?, time_status = ?, distance = ?, location_timestamp = ?
                WHERE id = ?
            """, (
                cleaned['location'],
                cleaned['timeStatus'],
                cleaned['distance'],
                cleaned.get('location_timestamp'),
                row['id'],
            ))
            updated += 1

        if not dry_run:
            conn.commit()

        action = "Would fix" if dry_run else "Fixed"
        logger.info(f"{action} {updated}/{len(rows)} records")
        return updated


def run_all(dry_run: bool = False):
    """Run all backfill steps in order."""
    init_schema()

    logger.info("=" * 60)
    logger.info("Starting full enrichment backfill")
    logger.info("=" * 60)

    backfill_resanitize(dry_run=dry_run)
    backfill_location_timestamps(dry_run=dry_run)
    backfill_distance_from_home(dry_run=dry_run)
    backfill_structured_addresses(dry_run=dry_run)
    backfill_trips(dry_run=dry_run)
    backfill_visits(dry_run=dry_run)

    logger.info("=" * 60)
    logger.info("Backfill complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AirTrackr retroactive data enrichment")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be done without changing data")
    parser.add_argument(
        "--step",
        choices=["timestamps", "distances", "addresses", "trips", "visits", "sanitize", "all"],
        default="all",
        help="Which backfill step to run (default: all)",
    )

    args = parser.parse_args()

    init_schema()

    if args.step == "timestamps":
        backfill_location_timestamps(dry_run=args.dry_run)
    elif args.step == "distances":
        backfill_distance_from_home(dry_run=args.dry_run)
    elif args.step == "addresses":
        backfill_structured_addresses(dry_run=args.dry_run)
    elif args.step == "trips":
        backfill_trips(dry_run=args.dry_run)
    elif args.step == "visits":
        backfill_visits(dry_run=args.dry_run)
    elif args.step == "sanitize":
        backfill_resanitize(dry_run=args.dry_run)
    else:
        run_all(dry_run=args.dry_run)
