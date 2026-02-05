#!/usr/bin/env python3
"""
Data retention and aggregation module for AirTrackr.

Aggregates raw location data into hourly/daily summaries,
then cleans up old raw records to keep database size manageable.

Can be run as a CLI script or called from the orchestrated tracker.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

from db import get_connection

logger = logging.getLogger(__name__)

# Default retention thresholds
DEFAULT_RAW_DAYS = 90
DEFAULT_HOURLY_DAYS = 365


def _load_retention_config() -> dict:
    """Load retention settings from config.json."""
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
            return config.get("database", {}).get("retention", {})
    except Exception:
        return {}


def aggregate_to_hourly(dry_run: bool = False) -> int:
    """
    Aggregate raw location data older than raw_data_days into hourly summaries.

    For each device and each hour, creates a summary with:
    - predominant_location (most frequent location in that hour)
    - lat/lon from the most frequent location
    - sample_count and unique_locations

    After successful aggregation, deletes the raw records.

    Args:
        dry_run: If True, only report what would be done without changing data

    Returns:
        Number of raw records processed
    """
    config = _load_retention_config()
    raw_days = config.get("raw_data_days", DEFAULT_RAW_DAYS)
    cutoff = (datetime.now() - timedelta(days=raw_days)).isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()

        # Find devices with data older than cutoff
        cursor.execute("""
            SELECT DISTINCT device_name FROM swift_locations
            WHERE timestamp < ?
        """, (cutoff,))
        devices = [row[0] for row in cursor.fetchall()]

        if not devices:
            logger.info("No raw records old enough for hourly aggregation")
            return 0

        total_processed = 0

        for device_name in devices:
            # Get all old records for this device
            cursor.execute("""
                SELECT id, location, latitude, longitude, timestamp
                FROM swift_locations
                WHERE device_name = ? AND timestamp < ?
                ORDER BY timestamp
            """, (device_name, cutoff))

            rows = cursor.fetchall()
            if not rows:
                continue

            # Group by hour
            hourly_groups: dict = {}
            for row in rows:
                ts = row["timestamp"]
                # Truncate to hour
                hour_key = ts[:13]  # "YYYY-MM-DD HH"
                if hour_key not in hourly_groups:
                    hourly_groups[hour_key] = []
                hourly_groups[hour_key].append(row)

            for hour_key, group_rows in hourly_groups.items():
                # Find predominant location
                locations = [r["location"] for r in group_rows if r["location"]]
                if locations:
                    location_counts = Counter(locations)
                    predominant = location_counts.most_common(1)[0][0]
                else:
                    predominant = None

                # Get coords from the most recent record with coords
                lat, lon = None, None
                for r in reversed(group_rows):
                    if r["latitude"] and r["longitude"]:
                        lat, lon = r["latitude"], r["longitude"]
                        break

                period_start = f"{hour_key}:00:00"
                period_end = f"{hour_key}:59:59"

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would aggregate {len(group_rows)} records "
                        f"for {device_name} at {hour_key}: {predominant}"
                    )
                else:
                    # Check if summary already exists
                    cursor.execute("""
                        SELECT id FROM location_summaries
                        WHERE device_name = ? AND period_start = ? AND period_type = 'hourly'
                    """, (device_name, period_start))

                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT INTO location_summaries
                            (device_name, period_start, period_end, period_type,
                             predominant_location, latitude, longitude,
                             sample_count, unique_locations)
                            VALUES (?, ?, ?, 'hourly', ?, ?, ?, ?, ?)
                        """, (
                            device_name, period_start, period_end,
                            predominant, lat, lon,
                            len(group_rows),
                            len(set(locations)) if locations else 0,
                        ))

                total_processed += len(group_rows)

            if not dry_run:
                # Delete the raw records we just aggregated
                ids_to_delete = [r["id"] for r in rows]
                # Delete in batches to avoid SQLite variable limit
                batch_size = 500
                for i in range(0, len(ids_to_delete), batch_size):
                    batch = ids_to_delete[i:i + batch_size]
                    placeholders = ",".join("?" * len(batch))
                    cursor.execute(
                        f"DELETE FROM swift_locations WHERE id IN ({placeholders})",
                        batch,
                    )

        if not dry_run:
            conn.commit()

        logger.info(f"Hourly aggregation: processed {total_processed} records for {len(devices)} devices")
        return total_processed


def aggregate_to_daily(dry_run: bool = False) -> int:
    """
    Aggregate hourly summaries older than hourly_summary_days into daily summaries.

    Args:
        dry_run: If True, only report what would be done

    Returns:
        Number of hourly summaries processed
    """
    config = _load_retention_config()
    hourly_days = config.get("hourly_summary_days", DEFAULT_HOURLY_DAYS)
    cutoff = (datetime.now() - timedelta(days=hourly_days)).isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT device_name FROM location_summaries
            WHERE period_type = 'hourly' AND period_start < ?
        """, (cutoff,))
        devices = [row[0] for row in cursor.fetchall()]

        if not devices:
            logger.info("No hourly summaries old enough for daily aggregation")
            return 0

        total_processed = 0

        for device_name in devices:
            cursor.execute("""
                SELECT id, predominant_location, latitude, longitude,
                       period_start, sample_count, unique_locations
                FROM location_summaries
                WHERE device_name = ? AND period_type = 'hourly' AND period_start < ?
                ORDER BY period_start
            """, (device_name, cutoff))

            rows = cursor.fetchall()
            if not rows:
                continue

            # Group by date
            daily_groups: dict = {}
            for row in rows:
                day_key = row["period_start"][:10]  # "YYYY-MM-DD"
                if day_key not in daily_groups:
                    daily_groups[day_key] = []
                daily_groups[day_key].append(row)

            for day_key, group_rows in daily_groups.items():
                # Find predominant location (weighted by sample_count)
                location_weights: dict = {}
                for r in group_rows:
                    loc = r["predominant_location"]
                    if loc:
                        location_weights[loc] = location_weights.get(loc, 0) + r["sample_count"]

                predominant = max(location_weights, key=location_weights.get) if location_weights else None

                # Get coords from predominant location
                lat, lon = None, None
                for r in group_rows:
                    if r["predominant_location"] == predominant and r["latitude"] and r["longitude"]:
                        lat, lon = r["latitude"], r["longitude"]
                        break

                total_samples = sum(r["sample_count"] for r in group_rows)
                all_unique = sum(r["unique_locations"] for r in group_rows)

                period_start = f"{day_key} 00:00:00"
                period_end = f"{day_key} 23:59:59"

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would aggregate {len(group_rows)} hourly summaries "
                        f"for {device_name} on {day_key}: {predominant}"
                    )
                else:
                    cursor.execute("""
                        SELECT id FROM location_summaries
                        WHERE device_name = ? AND period_start = ? AND period_type = 'daily'
                    """, (device_name, period_start))

                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT INTO location_summaries
                            (device_name, period_start, period_end, period_type,
                             predominant_location, latitude, longitude,
                             sample_count, unique_locations)
                            VALUES (?, ?, ?, 'daily', ?, ?, ?, ?, ?)
                        """, (
                            device_name, period_start, period_end,
                            predominant, lat, lon,
                            total_samples, all_unique,
                        ))

                total_processed += len(group_rows)

            if not dry_run:
                # Delete aggregated hourly summaries
                ids_to_delete = [r["id"] for r in rows]
                batch_size = 500
                for i in range(0, len(ids_to_delete), batch_size):
                    batch = ids_to_delete[i:i + batch_size]
                    placeholders = ",".join("?" * len(batch))
                    cursor.execute(
                        f"DELETE FROM location_summaries WHERE id IN ({placeholders})",
                        batch,
                    )

        if not dry_run:
            conn.commit()

        logger.info(f"Daily aggregation: processed {total_processed} hourly summaries for {len(devices)} devices")
        return total_processed


def run_retention(dry_run: bool = False, vacuum: bool = True):
    """
    Run the full retention pipeline:
    1. Aggregate raw data → hourly summaries
    2. Aggregate hourly summaries → daily summaries
    3. Optionally VACUUM the database

    Args:
        dry_run: If True, only report what would be done
        vacuum: If True, VACUUM after deletes
    """
    logger.info("Starting retention pipeline...")

    hourly_count = aggregate_to_hourly(dry_run=dry_run)
    daily_count = aggregate_to_daily(dry_run=dry_run)

    if not dry_run and vacuum and (hourly_count > 0 or daily_count > 0):
        logger.info("Running VACUUM...")
        with get_connection() as conn:
            conn.execute("VACUUM")
        logger.info("VACUUM complete")

    logger.info(
        f"Retention complete: {hourly_count} raw records aggregated to hourly, "
        f"{daily_count} hourly summaries aggregated to daily"
    )


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="AirTrackr data retention and aggregation")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be done without changing data")
    parser.add_argument("--no-vacuum", action="store_true", help="Skip VACUUM after cleanup")
    parser.add_argument(
        "--hourly-only", action="store_true", help="Only run hourly aggregation"
    )
    parser.add_argument(
        "--daily-only", action="store_true", help="Only run daily aggregation"
    )

    args = parser.parse_args()

    if args.hourly_only:
        aggregate_to_hourly(dry_run=args.dry_run)
    elif args.daily_only:
        aggregate_to_daily(dry_run=args.dry_run)
    else:
        run_retention(dry_run=args.dry_run, vacuum=not args.no_vacuum)
