#!/usr/bin/env python3
"""
AirTag Tracker using Swift Accessibility API

This module provides a Python interface to extract AirTag location data
from the Find My app using macOS Accessibility APIs via a Swift helper.

Key Features:
- Direct data extraction without screenshots
- Structured JSON output from Swift extractor
- SQLite database storage with history tracking
- Automatic retry and error handling
- Scheduled tracking support

Requirements:
- macOS with Find My app
- Swift extractor compiled (swift/airtag_extractor)
- Accessibility permissions for Terminal/iTerm
"""

import json
import sqlite3
import subprocess
import os
import sys
import time
import logging
import schedule
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from geocoding import Geocoder
from db import get_connection, init_schema, is_duplicate, sanitize_device_data, resolve_location_alias
from enrichment import compute_distance_from_home, update_visits, detect_trips

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class SwiftAirTagTracker:
    """
    Main tracker class that interfaces with the Swift extractor
    and manages database storage of AirTag locations.
    """
    
    def __init__(self):
        """Initialize the tracker with Swift extractor path."""
        self.swift_extractor = Path(__file__).parent / "swift" / "airtag_extractor"
        self.geocoder = Geocoder()

        # The binary MUST be pre-compiled as part of the deployment process.
        if not self.swift_extractor.exists() or not os.access(self.swift_extractor, os.X_OK):
            error_msg = (
                f"Swift extractor not found or not executable at {self.swift_extractor}. "
                "Please compile it using the 'swift/build_universal.sh' script before running the tracker."
            )
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        # Initialize database schema via shared module
        init_schema()
        logger.info("Initialized tracker")
    
    def extract_locations(self, retry_count: int = 3) -> List[Dict]:
        """
        Extract AirTag locations using the Swift accessibility API.
        
        Args:
            retry_count: Number of retries if extraction fails
            
        Returns:
            List of device dictionaries with location data
        """
        for attempt in range(retry_count):
            try:
                # Run the Swift extractor with timeout
                result = subprocess.run(
                    [str(self.swift_extractor)],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10  # 10 second timeout
                )
                
                # Parse JSON from stdout (stderr has status messages)
                if result.stdout.strip():
                    devices = json.loads(result.stdout)
                    logger.info(f"Successfully extracted {len(devices)} devices")
                    return devices
                else:
                    logger.warning("No JSON output from Swift extractor")
                    return []
                    
            except subprocess.TimeoutExpired:
                logger.error(f"Swift extractor timed out (attempt {attempt + 1}/{retry_count})")
                
            except subprocess.CalledProcessError as e:
                logger.error(f"Swift extractor failed: {e}")
                if e.stderr:
                    logger.error(f"Error output: {e.stderr}")
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON: {e}")
                logger.debug(f"Raw output: {result.stdout[:200]}...")
                
            except Exception as e:
                logger.error(f"Unexpected error: {type(e).__name__}: {e}")
            
            # Wait before retry
            if attempt < retry_count - 1:
                time.sleep(2)
        
        logger.error(f"Failed to extract locations after {retry_count} attempts")
        return []
    
    def save_locations(self, devices: List[Dict]) -> int:
        """
        Save extracted device locations to the database.

        Args:
            devices: List of device dictionaries from Swift extractor

        Returns:
            Number of records saved
        """
        if not devices:
            return 0

        saved_count = 0

        with get_connection() as conn:
            try:
                cursor = conn.cursor()

                for device_data in devices:
                    # Sanitize: fix decimal-distance parsing, time-in-location,
                    # and skip "No location found" noise
                    cleaned = sanitize_device_data(dict(device_data))
                    if cleaned is None:
                        logger.debug(f"Skipping {device_data['name']}: no usable location")
                        continue

                    # Parse extracted_at timestamp from ISO format
                    extracted_at = cleaned.get('extractedAt', '')
                    if extracted_at:
                        extracted_at = extracted_at.replace('T', ' ').replace('Z', '')

                    device_name = cleaned['name']
                    location_text = cleaned['location']

                    # Skip duplicates within 2-minute window
                    if is_duplicate(conn, device_name, location_text):
                        logger.debug(f"Skipping duplicate: {device_name} at {location_text}")
                        continue

                    # Resolve alias (e.g. "Home" → "Onderstraat 7, 9000 Ghent")
                    geocode_text = resolve_location_alias(location_text)

                    # Geocode the resolved address
                    latitude, longitude = None, None
                    try:
                        latitude, longitude = self.geocoder.geocode(geocode_text)
                        if latitude and longitude:
                            logger.debug(f"Geocoded {location_text} -> ({latitude:.6f}, {longitude:.6f})")
                    except Exception as e:
                        logger.warning(f"Geocoding failed for '{geocode_text}': {e}")

                    # Computed timestamp from relative time (e.g. "15 min ago" → absolute)
                    location_timestamp = cleaned.get('location_timestamp')

                    # Distance from home
                    dist_home = None
                    if latitude is not None and longitude is not None:
                        try:
                            dist_home = compute_distance_from_home(latitude, longitude)
                        except Exception as e:
                            logger.debug(f"Could not compute distance from home: {e}")

                    # Battery status (from Swift extractor, may be None)
                    battery_status = cleaned.get('batteryStatus')

                    # Insert location record
                    cursor.execute('''
                        INSERT INTO swift_locations
                        (device_name, location, time_status, distance, latitude, longitude,
                         raw_data, extracted_at, location_timestamp,
                         distance_from_home_km, battery_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        device_name,
                        location_text,
                        cleaned['timeStatus'],
                        cleaned['distance'],
                        latitude,
                        longitude,
                        json.dumps(device_data),  # Store original raw data for debugging
                        extracted_at,
                        location_timestamp,
                        dist_home,
                        battery_status,
                    ))

                    # Update or insert device summary
                    cursor.execute('''
                        INSERT INTO swift_devices (device_name, last_location, update_count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(device_name) DO UPDATE SET
                            last_seen = CURRENT_TIMESTAMP,
                            last_location = excluded.last_location,
                            update_count = update_count + 1
                    ''', (
                        device_name,
                        location_text
                    ))

                    saved_count += 1

                    # Track visit (dwell time)
                    ts = location_timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    try:
                        update_visits(device_name, location_text, latitude, longitude, ts)
                    except Exception as e:
                        logger.debug(f"Visit tracking failed for {device_name}: {e}")

                conn.commit()
                logger.info(f"Saved {saved_count}/{len(devices)} location updates")

            except Exception as e:
                logger.error(f"Error saving locations: {e}")
                conn.rollback()
                return 0

        return saved_count
    
    def track_once(self) -> bool:
        """
        Perform a single tracking operation.
        
        Returns:
            True if successful, False otherwise
        """
        logger.info("Starting location extraction...")
        
        # Extract locations from Find My
        devices = self.extract_locations()
        
        if not devices:
            logger.warning("No devices found or extraction failed")
            return False
        
        # Log summary
        logger.info(f"Found {len(devices)} devices:")
        for device in devices:
            status = f"{device['timeStatus']}, {device['distance']}" if device['distance'] != '-' else device['timeStatus']
            logger.info(f"  - {device['name']}: {device['location']} ({status})")
        
        # Save to database
        saved = self.save_locations(devices)

        # Detect trips for each device
        if saved > 0:
            device_names = {d['name'] for d in devices}
            for name in device_names:
                try:
                    detect_trips(name, since_minutes=10)
                except Exception as e:
                    logger.debug(f"Trip detection failed for {name}: {e}")

        return saved > 0
    
    def get_recent_locations(self, limit: int = 20, device_name: Optional[str] = None) -> List[sqlite3.Row]:
        """
        Retrieve recent location updates from the database.
        
        Args:
            limit: Maximum number of records to return
            device_name: Filter by specific device name (optional)
            
        Returns:
            List of location records
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            
            if device_name:
                cursor.execute('''
                    SELECT device_name, location, time_status, distance, timestamp, extracted_at
                    FROM swift_locations
                    WHERE device_name = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (device_name, limit))
            else:
                cursor.execute('''
                    SELECT device_name, location, time_status, distance, timestamp, extracted_at
                    FROM swift_locations
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (limit,))
            
            return cursor.fetchall()
    
    def get_device_summary(self) -> List[sqlite3.Row]:
        """
        Get summary information for all tracked devices.
        
        Returns:
            List of device summary records
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    device_name,
                    first_seen,
                    last_seen,
                    last_location,
                    update_count,
                    ROUND((julianday('now') - julianday(last_seen)) * 24 * 60, 1) as minutes_ago
                FROM swift_devices
                ORDER BY last_seen DESC
            ''')
            
            return cursor.fetchall()
    
    def cleanup_old_records(self, days_to_keep: int = 30) -> int:
        """
        Remove location records older than specified days.
        
        Args:
            days_to_keep: Number of days of history to retain
            
        Returns:
            Number of records deleted
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        with get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM swift_locations
                WHERE timestamp < ?
            ''', (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} records older than {days_to_keep} days")
            
        return deleted_count
    
    def run_scheduled(self, interval_minutes: int = 5):
        """
        Run tracking on a schedule.
        
        Args:
            interval_minutes: Minutes between tracking runs
        """
        logger.info(f"Starting scheduled tracking every {interval_minutes} minutes")
        logger.info("Press Ctrl+C to stop")
        
        # Run once immediately
        self.track_once()
        
        # Schedule periodic runs
        schedule.every(interval_minutes).minutes.do(self.track_once)
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Scheduled tracking stopped by user")


def main():
    """Main entry point for command-line usage."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Track AirTag locations using Find My accessibility API"
    )
    parser.add_argument(
        '--schedule', '-s',
        type=int,
        metavar='MINUTES',
        help='Run on schedule every N minutes'
    )
    parser.add_argument(
        '--history', '-H',
        action='store_true',
        help='Show location history'
    )
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Show device summary'
    )
    parser.add_argument(
        '--cleanup',
        type=int,
        metavar='DAYS',
        help='Clean up records older than N days'
    )
    parser.add_argument(
        '--device',
        help='Filter by device name (for history)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='Limit number of history records (default: 20)'
    )

    args = parser.parse_args()

    # Initialize tracker
    try:
        tracker = SwiftAirTagTracker()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    # Handle different modes
    if args.cleanup:
        deleted = tracker.cleanup_old_records(args.cleanup)
        print(f"Cleaned up {deleted} old records")
        
    elif args.history:
        print("\n📊 Location History")
        print("=" * 80)
        
        records = tracker.get_recent_locations(args.limit, args.device)
        for record in records:
            timestamp = record['timestamp']
            device = record['device_name']
            location = record['location']
            status = record['time_status']
            distance = record['distance']
            
            print(f"{timestamp} | {device}: {location} ({status}, {distance})")
    
    elif args.summary:
        print("\n📱 Device Summary")
        print("=" * 80)
        
        devices = tracker.get_device_summary()
        for device in devices:
            name = device['device_name']
            location = device['last_location']
            updates = device['update_count']
            minutes_ago = device['minutes_ago'] or 0
            
            if minutes_ago < 60:
                time_str = f"{int(minutes_ago)} min ago"
            elif minutes_ago < 1440:
                time_str = f"{int(minutes_ago / 60)} hours ago"
            else:
                time_str = f"{int(minutes_ago / 1440)} days ago"
            
            print(f"{name:<30} {location:<40} ({updates} updates, {time_str})")
    
    elif args.schedule:
        tracker.run_scheduled(args.schedule)
        
    else:
        # Single run
        success = tracker.track_once()
        if not success:
            sys.exit(1)


if __name__ == "__main__":
    main()