#!/usr/bin/env python3
"""
Orchestrated AirTag Tracker with Tab Cycling

This tracker automatically cycles through all Find My tabs (People, Devices, Items)
to comprehensively track all entities in the Find My ecosystem.

Automation Sequence (per cycle):
1. Pause 5 seconds (initial load time)
2. Switch to People tab (Cmd+1) → Wait 5s → Extract → Wait 30s
3. Switch to Devices tab (Cmd+2) → Wait 5s → Extract → Wait 30s
4. Switch to Items tab (Cmd+3) → Wait 5s → Extract → Wait 60s
5. Repeat

Total cycle time: ~3 minutes
"""

import json
import sqlite3
import subprocess
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from contextlib import contextmanager

from findmy_automation import FindMyAutomation, DeviceType
from geocoding import Geocoder

# Configure logging with DEBUG level for verbose output
# Create logs directory if it doesn't exist
Path("logs").mkdir(exist_ok=True)

# Configure logging to both file and console
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('logs/tracker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class OrchestratedAirTagTracker:
    """
    Orchestrated tracker that cycles through Find My tabs to capture
    all people, devices, and items.
    """

    # Timing configuration (in seconds)
    INITIAL_PAUSE = 5      # Initial pause before starting
    TAB_LOAD_TIME = 5      # Time to wait after switching tabs
    EXTRACT_PAUSE = 30     # Pause after extracting data
    CYCLE_END_PAUSE = 60   # Pause at end of cycle before repeating

    def __init__(self, db_path: str = "database/airtracker.db"):
        """
        Initialize the orchestrated tracker.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.swift_extractor = Path(__file__).parent / "swift" / "airtag_extractor"
        self.automation = FindMyAutomation()
        self.geocoder = Geocoder()

        # Ensure database directory exists
        Path(db_path).parent.mkdir(exist_ok=True)

        # Verify Swift extractor exists
        if not self.swift_extractor.exists() or not os.access(self.swift_extractor, os.X_OK):
            error_msg = (
                f"Swift extractor not found or not executable at {self.swift_extractor}. "
                "Please compile it using the 'swift/build_universal.sh' script."
            )
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        # Initialize database schema
        self._init_database()
        logger.info(f"Initialized orchestrated tracker with database: {self.db_path}")

    def _init_database(self):
        """Initialize the database schema."""
        with self._get_db_connection() as conn:
            cursor = conn.cursor()

            # Create swift_locations table
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

            # Create swift_devices table
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

            # Create indexes
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

            conn.commit()

    @contextmanager
    def _get_db_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def extract_locations_for_tab(self, device_type: DeviceType, retry_count: int = 3) -> List[Dict]:
        """
        Extract locations from the current tab.

        Args:
            device_type: Type of entities in current tab
            retry_count: Number of retries if extraction fails

        Returns:
            List of device dictionaries with location data
        """
        for attempt in range(retry_count):
            try:
                result = subprocess.run(
                    [str(self.swift_extractor)],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10
                )

                if result.stdout.strip():
                    devices = json.loads(result.stdout)
                    logger.info(f"Successfully extracted {len(devices)} {device_type}(s)")
                    return devices
                else:
                    logger.warning(f"No JSON output from Swift extractor for {device_type} tab")
                    return []

            except subprocess.TimeoutExpired:
                logger.error(f"Swift extractor timed out for {device_type} tab (attempt {attempt + 1}/{retry_count})")

            except subprocess.CalledProcessError as e:
                logger.error(f"Swift extractor failed for {device_type} tab: {e}")
                if e.stderr:
                    logger.error(f"Error output: {e.stderr}")

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON for {device_type} tab: {e}")

            except Exception as e:
                logger.error(f"Unexpected error extracting {device_type} tab: {type(e).__name__}: {e}")

            if attempt < retry_count - 1:
                time.sleep(2)

        logger.error(f"Failed to extract {device_type} tab after {retry_count} attempts")
        return []

    def save_locations(self, devices: List[Dict], device_type: DeviceType) -> int:
        """
        Save extracted device locations to the database.

        Args:
            devices: List of device dictionaries from Swift extractor
            device_type: Type of entities (person, device, or item)

        Returns:
            Number of records saved
        """
        if not devices:
            return 0

        saved_count = 0

        with self._get_db_connection() as conn:
            try:
                cursor = conn.cursor()

                for device_data in devices:
                    # Parse extracted_at timestamp
                    extracted_at = device_data.get('extractedAt', '')
                    if extracted_at:
                        extracted_at = extracted_at.replace('T', ' ').replace('Z', '')

                    # Geocode the location
                    location_text = device_data['location']
                    latitude, longitude = None, None

                    if location_text and location_text != "No location found":
                        try:
                            latitude, longitude = self.geocoder.geocode(location_text)
                            if latitude and longitude:
                                logger.debug(f"Geocoded {location_text} -> ({latitude:.6f}, {longitude:.6f})")
                        except Exception as e:
                            logger.warning(f"Geocoding failed for '{location_text}': {e}")

                    # Insert location record with device_type
                    cursor.execute('''
                        INSERT INTO swift_locations
                        (device_name, location, time_status, distance, latitude, longitude, device_type, raw_data, extracted_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        device_data['name'],
                        device_data['location'],
                        device_data['timeStatus'],
                        device_data['distance'],
                        latitude,
                        longitude,
                        device_type,
                        json.dumps(device_data),
                        extracted_at
                    ))

                    # Update or insert device summary with device_type
                    cursor.execute('''
                        INSERT INTO swift_devices (device_name, device_type, last_location, update_count)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(device_name) DO UPDATE SET
                            last_seen = CURRENT_TIMESTAMP,
                            last_location = excluded.last_location,
                            device_type = excluded.device_type,
                            update_count = update_count + 1
                    ''', (
                        device_data['name'],
                        device_type,
                        device_data['location']
                    ))

                    saved_count += 1

                conn.commit()
                logger.info(f"Saved {saved_count}/{len(devices)} {device_type} updates")

            except sqlite3.Error as e:
                logger.error(f"Database error for {device_type} tab: {e}")
                conn.rollback()
                return 0
            except Exception as e:
                logger.error(f"Unexpected error saving {device_type} tab: {e}")
                conn.rollback()
                return 0

        return saved_count

    def process_tab(self, device_type: DeviceType) -> bool:
        """
        Process a single tab: switch, wait, extract, save.

        Args:
            device_type: The type of tab to process

        Returns:
            True if successful, False otherwise
        """
        tab_names = {'person': 'People', 'device': 'Devices', 'item': 'Items'}
        tab_name = tab_names[device_type]

        logger.info(f"{'='*60}")
        logger.info(f"Processing {tab_name} tab...")
        logger.info(f"{'='*60}")

        # Ensure Find My is active and switch to tab
        if not self.automation.ensure_find_my_running():
            logger.error(f"Failed to ensure Find My is running for {tab_name} tab")
            return False

        self.automation.activate_find_my()

        if not self.automation.switch_to_tab(device_type):
            logger.error(f"Failed to switch to {tab_name} tab")
            return False

        # Wait for tab to load
        logger.info(f"Waiting {self.TAB_LOAD_TIME}s for {tab_name} tab to load...")
        time.sleep(self.TAB_LOAD_TIME)

        # Extract locations
        devices = self.extract_locations_for_tab(device_type)

        if not devices:
            logger.warning(f"No {device_type}s found in {tab_name} tab")
            return False

        # Log summary
        logger.info(f"Found {len(devices)} {device_type}(s):")
        for device in devices:
            status = f"{device['timeStatus']}, {device['distance']}" if device['distance'] != '-' else device['timeStatus']
            logger.info(f"  - {device['name']}: {device['location']} ({status})")

        # Save to database
        saved = self.save_locations(devices, device_type)
        return saved > 0

    def run_single_cycle(self) -> bool:
        """
        Run a single complete cycle through all tabs.

        Returns:
            True if at least one tab was successfully processed
        """
        logger.info("\n" + "=" * 70)
        logger.info("🔄 STARTING NEW TRACKING CYCLE")
        logger.info("=" * 70)

        # Initial pause
        logger.info(f"Initial pause: {self.INITIAL_PAUSE}s...")
        time.sleep(self.INITIAL_PAUSE)

        success_count = 0
        tabs = [
            ('person', 'People'),
            ('device', 'Devices'),
            ('item', 'Items')
        ]

        for i, (device_type, tab_name) in enumerate(tabs):
            # Process the tab
            if self.process_tab(device_type):
                success_count += 1

            # Pause after extraction (except for the last tab, which uses cycle end pause)
            if i < len(tabs) - 1:
                logger.info(f"Pausing {self.EXTRACT_PAUSE}s before next tab...\n")
                time.sleep(self.EXTRACT_PAUSE)

        # End of cycle pause
        logger.info(f"\n✅ Cycle complete! {success_count}/{len(tabs)} tabs processed successfully")
        logger.info(f"Pausing {self.CYCLE_END_PAUSE}s before next cycle...\n")

        return success_count > 0

    def run_continuous(self):
        """Run continuous tracking with tab cycling."""
        logger.info("=" * 70)
        logger.info("🚀 STARTING ORCHESTRATED AIRTRACKER (CONTINUOUS MODE)")
        logger.info("=" * 70)
        logger.info("Configuration:")
        logger.info(f"  - Initial pause: {self.INITIAL_PAUSE}s")
        logger.info(f"  - Tab load time: {self.TAB_LOAD_TIME}s")
        logger.info(f"  - Extract pause: {self.EXTRACT_PAUSE}s")
        logger.info(f"  - Cycle end pause: {self.CYCLE_END_PAUSE}s")
        logger.info(f"  - Approximate cycle time: ~3 minutes")
        logger.info("")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 70)

        try:
            while True:
                self.run_single_cycle()
                time.sleep(self.CYCLE_END_PAUSE)

        except KeyboardInterrupt:
            logger.info("\n\n🛑 Orchestrated tracking stopped by user")

    def run_scheduled(self, interval_minutes: int):
        """Run scheduled tracking with tab cycling."""
        import schedule

        logger.info("=" * 70)
        logger.info("🚀 STARTING ORCHESTRATED AIRTRACKER (SCHEDULED MODE)")
        logger.info("=" * 70)
        logger.info(f"Schedule: Every {interval_minutes} minute(s)")
        logger.info("Configuration:")
        logger.info(f"  - Initial pause: {self.INITIAL_PAUSE}s")
        logger.info(f"  - Tab load time: {self.TAB_LOAD_TIME}s")
        logger.info(f"  - Extract pause: {self.EXTRACT_PAUSE}s")
        logger.info(f"  - Cycle end pause: {self.CYCLE_END_PAUSE}s")
        logger.info(f"  - Approximate cycle time: ~3 minutes")
        logger.info("")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 70)

        # Schedule the tracking job
        schedule.every(interval_minutes).minutes.do(self.run_single_cycle)

        # Run first cycle immediately
        logger.info("\n⏰ Running first cycle immediately...")
        self.run_single_cycle()

        # Then run on schedule
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n\n🛑 Orchestrated tracking stopped by user")


def main():
    """Main entry point for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Orchestrated AirTag tracker with automatic tab cycling"
    )
    parser.add_argument(
        '--database', '-d',
        default='database/airtracker.db',
        help='Path to SQLite database (default: database/airtracker.db)'
    )
    parser.add_argument(
        '--single-cycle',
        action='store_true',
        help='Run a single cycle and exit (useful for testing)'
    )
    parser.add_argument(
        '--schedule', '-s',
        type=int,
        metavar='MINUTES',
        help='Run on a schedule (every N minutes) instead of continuous'
    )

    args = parser.parse_args()

    # Initialize tracker
    try:
        tracker = OrchestratedAirTagTracker(args.database)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Run
    if args.single_cycle:
        success = tracker.run_single_cycle()
        sys.exit(0 if success else 1)
    elif args.schedule:
        tracker.run_scheduled(args.schedule)
    else:
        tracker.run_continuous()


if __name__ == "__main__":
    main()
