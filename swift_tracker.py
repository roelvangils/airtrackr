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
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager
from geocoding import Geocoder

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
    
    def __init__(self, db_path: str = "database/airtracker.db"):
        """
        Initialize the tracker with database and Swift extractor paths.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.swift_extractor = Path(__file__).parent / "swift" / "airtag_extractor"
        self.geocoder = Geocoder()  # Initialize geocoder
        
        # Ensure database directory exists
        Path(db_path).parent.mkdir(exist_ok=True)
        
        # Verify Swift extractor exists or compile it
        if not self.swift_extractor.exists():
            logger.warning(f"Swift extractor not found at {self.swift_extractor}")
            self._compile_swift_extractor()
        else:
            # Check if the binary works on this architecture
            if not self._test_swift_extractor():
                logger.warning("Swift extractor incompatible with current architecture, recompiling...")
                self._compile_swift_extractor()
        
        # Make sure extractor is executable
        self.swift_extractor.chmod(0o755)
        
        # Initialize database schema
        self._init_database()
        logger.info(f"Initialized tracker with database: {self.db_path}")
    
    def _init_database(self):
        """
        Initialize the database schema for storing AirTag locations.
        Creates tables and indexes if they don't exist.
        """
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Create swift_locations table for raw location data
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS swift_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_name TEXT NOT NULL,
                    location TEXT,
                    time_status TEXT,
                    distance TEXT,
                    latitude REAL,
                    longitude REAL,
                    raw_data TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    extracted_at TIMESTAMP  -- From Swift extractor
                )
            ''')
            
            # Check if extracted_at column exists, add if missing
            cursor.execute("PRAGMA table_info(swift_locations)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'extracted_at' not in columns:
                cursor.execute('ALTER TABLE swift_locations ADD COLUMN extracted_at TIMESTAMP')
                logger.info("Added extracted_at column to swift_locations table")
            
            # Create devices summary table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS swift_devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_name TEXT UNIQUE NOT NULL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_location TEXT,
                    update_count INTEGER DEFAULT 0
                )
            ''')
            
            # Create indexes for performance
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_swift_locations_device_name 
                ON swift_locations(device_name)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_swift_locations_timestamp 
                ON swift_locations(timestamp DESC)
            ''')
            
            # Only create extracted_at index if column exists
            if 'extracted_at' in columns or 'extracted_at' not in columns:  # Will exist after ALTER
                try:
                    cursor.execute('''
                        CREATE INDEX IF NOT EXISTS idx_swift_locations_extracted_at 
                        ON swift_locations(extracted_at)
                    ''')
                except sqlite3.OperationalError:
                    # Index might already exist or column might not exist in some edge case
                    pass
            
            conn.commit()
            logger.debug("Database schema initialized")
    
    def _test_swift_extractor(self) -> bool:
        """
        Test if the Swift extractor binary works on current architecture.
        
        Returns:
            True if binary works, False if incompatible
        """
        try:
            # Try to run with --help or minimal test
            result = subprocess.run(
                [str(self.swift_extractor), "--version"],
                capture_output=True,
                timeout=2
            )
            # If it doesn't crash, it's compatible
            return result.returncode != 86  # 86 = Bad CPU type
        except Exception:
            return False
    
    def _compile_swift_extractor(self):
        """
        Compile the Swift extractor for the current architecture or as universal binary.
        """
        swift_dir = self.swift_extractor.parent
        swift_source = swift_dir / "airtag_extractor.swift"
        build_script = swift_dir / "build_universal.sh"
        
        if not swift_source.exists():
            raise FileNotFoundError(f"Swift source not found at {swift_source}")
        
        logger.info("Compiling Swift extractor...")
        
        # Check if universal build script exists
        if build_script.exists():
            logger.info("Using universal build script...")
            try:
                result = subprocess.run(
                    ["bash", str(build_script)],
                    cwd=swift_dir,
                    capture_output=True,
                    text=True,
                    check=True
                )
                logger.info("Successfully compiled universal binary")
                return
            except subprocess.CalledProcessError as e:
                logger.warning(f"Universal build failed: {e.stderr}")
                # Fall back to simple compilation
        
        # Simple compilation for current architecture
        logger.info("Compiling for current architecture...")
        try:
            result = subprocess.run(
                ["swiftc", str(swift_source), "-o", str(self.swift_extractor)],
                cwd=swift_dir,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info("Successfully compiled Swift extractor")
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to compile Swift extractor: {e.stderr}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    @contextmanager
    def _get_db_connection(self):
        """
        Context manager for database connections with automatic cleanup.
        
        Yields:
            sqlite3.Connection: Database connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
        finally:
            conn.close()
    
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
        
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            
            for device_data in devices:
                try:
                    # Parse extracted_at timestamp from ISO format
                    extracted_at = device_data.get('extractedAt', '')
                    if extracted_at:
                        # Convert ISO 8601 to SQLite timestamp
                        extracted_at = extracted_at.replace('T', ' ').replace('Z', '')
                    
                    # Geocode the location if it's not "No location found"
                    location_text = device_data['location']
                    latitude, longitude = None, None
                    
                    if location_text and location_text != "No location found":
                        try:
                            latitude, longitude = self.geocoder.geocode(location_text)
                            if latitude and longitude:
                                logger.debug(f"Geocoded {location_text} -> ({latitude:.6f}, {longitude:.6f})")
                        except Exception as e:
                            logger.warning(f"Geocoding failed for '{location_text}': {e}")
                    
                    # Insert location record
                    cursor.execute('''
                        INSERT INTO swift_locations 
                        (device_name, location, time_status, distance, latitude, longitude, raw_data, extracted_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        device_data['name'],
                        device_data['location'],
                        device_data['timeStatus'],
                        device_data['distance'],
                        latitude,
                        longitude,
                        json.dumps(device_data),
                        extracted_at
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
                        device_data['name'],
                        device_data['location']
                    ))
                    
                    saved_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to save device {device_data.get('name', 'Unknown')}: {e}")
                    continue
            
            conn.commit()
        
        logger.info(f"Saved {saved_count}/{len(devices)} location updates")
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
        with self._get_db_connection() as conn:
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
        with self._get_db_connection() as conn:
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
        
        with self._get_db_connection() as conn:
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
        '--database', '-d',
        default='database/airtracker.db',
        help='Path to SQLite database (default: database/airtracker.db)'
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
        tracker = SwiftAirTagTracker(args.database)
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