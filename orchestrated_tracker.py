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
import subprocess
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional

from findmy_automation import FindMyAutomation, DeviceType
from geocoding import Geocoder
from db import get_connection, init_schema, is_duplicate, sanitize_device_data, resolve_location_alias, resolve_device_alias
from enrichment import compute_distance_from_home, update_visits, detect_trips

# Night mode / temp wake flags
NIGHT_FLAG = Path("/tmp/airtrackr_night_mode")
TEMP_WAKE_FLAG = Path("/tmp/airtrackr_temp_wake")

# Configure logging - file only to avoid duplicates
# Console shows key events via print(), detailed logs go to file
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('logs/tracker.log')
    ]
)
logger = logging.getLogger(__name__)

# Singleton: PID file to ensure only one tracker instance runs
PID_FILE = Path("/tmp/airtrackr_tracker.pid")


def ensure_singleton():
    """
    Ensure only one tracker instance runs at a time.
    Kills any existing instance before starting.
    """
    import signal

    current_pid = os.getpid()

    # Check if another instance is running
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if old_pid != current_pid:
                # Check if process is still running
                try:
                    os.kill(old_pid, 0)  # Signal 0 = check if process exists
                    # Process exists, kill it
                    logger.info(f"[SINGLETON] Killing existing tracker instance (PID {old_pid})")
                    os.kill(old_pid, signal.SIGTERM)
                    time.sleep(2)
                    # Force kill if still running
                    try:
                        os.kill(old_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                except ProcessLookupError:
                    # Process doesn't exist, stale PID file
                    logger.info(f"[SINGLETON] Removing stale PID file (PID {old_pid} not running)")
        except (ValueError, FileNotFoundError):
            pass

    # Write our PID
    PID_FILE.write_text(str(current_pid))
    logger.info(f"[SINGLETON] Tracker started with PID {current_pid}")

    # Clean up PID file on exit
    import atexit
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))


class OrchestratedAirTagTracker:
    """
    Orchestrated tracker that cycles through Find My tabs to capture
    all people, devices, and items.
    """

    # Timing configuration (in seconds)
    INITIAL_PAUSE = 5      # Initial pause before starting
    TAB_LOAD_TIME = {      # Per-tab wait time (Find My needs time to refresh from iCloud)
        'person': 15,      # People tab refreshes relatively fast
        'device': 30,      # Devices tab is much slower to fetch updated locations
        'item': 15,        # Items (AirTags) refresh reasonably fast
    }
    EXTRACT_PAUSE = 15     # Pause after extracting data
    CYCLE_END_PAUSE = 60   # Pause at end of cycle before repeating

    # Failure recovery settings
    MAX_CONSECUTIVE_FAILURES = 5
    FINDMY_RESTART_COOLDOWN = 300  # 5 minutes between restarts

    # Keep-alive settings
    KEEPALIVE_INTERVAL = 1800  # 30 minutes - force refresh Find My
    PREEMPTIVE_RESTART_INTERVAL = 14400  # 4 hours - restart Find My to prevent stale state

    # Path to fix script
    FIX_FINDMY_SCRIPT = Path(__file__).parent / "imac" / "fix_findmy_window.sh"

    def __init__(self):
        """Initialize the orchestrated tracker."""
        self.swift_extractor = Path(__file__).parent / "swift" / "airtag_extractor"
        self.automation = FindMyAutomation()
        self.geocoder = Geocoder()

        # Failure tracking for auto-recovery
        self.consecutive_failures = 0
        self.last_findmy_restart: Optional[datetime] = None

        # Keep-alive tracking
        self.last_keepalive: Optional[datetime] = None
        self.last_preemptive_restart: Optional[datetime] = None

        # Verify Swift extractor exists
        if not self.swift_extractor.exists() or not os.access(self.swift_extractor, os.X_OK):
            error_msg = (
                f"Swift extractor not found or not executable at {self.swift_extractor}. "
                "Please compile it using the 'swift/build_universal.sh' script."
            )
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        # Initialize database schema via shared module
        init_schema()
        logger.info("Initialized orchestrated tracker")

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

    def save_locations(self, devices: List[Dict], device_type: DeviceType) -> tuple:
        """
        Save extracted device locations to the database.

        Args:
            devices: List of device dictionaries from Swift extractor
            device_type: Type of entities (person, device, or item)

        Returns:
            Tuple of (number of records saved, set of resolved device names)
        """
        if not devices:
            return 0, set()

        saved_count = 0
        saved_device_names = set()

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

                    # Parse extracted_at timestamp (Swift outputs UTC with Z suffix)
                    extracted_at = cleaned.get('extractedAt', '')
                    if extracted_at:
                        # Parse UTC timestamp and convert to local timezone
                        try:
                            utc_dt = datetime.fromisoformat(extracted_at.replace('Z', '+00:00'))
                            local_dt = utc_dt.astimezone()  # Convert to system timezone
                            extracted_at = local_dt.strftime('%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            # Fallback for unexpected formats
                            extracted_at = extracted_at.replace('T', ' ').replace('Z', '')

                    device_name = resolve_device_alias(cleaned['name'])
                    location_text = cleaned['location']

                    # Skip duplicates within 2-minute window
                    if is_duplicate(conn, device_name, location_text):
                        logger.debug(f"Skipping duplicate: {device_name} at {location_text}")
                        # Update last_seen in swift_devices, even without a new location record
                        cursor.execute('''
                            UPDATE swift_devices SET last_seen = CURRENT_TIMESTAMP
                            WHERE device_name = ?
                        ''', (device_name,))
                        continue

                    # Resolve alias (e.g. "Home" → "Onderstraat 7, 9000 Ghent")
                    geocode_text = resolve_location_alias(location_text)

                    # Geocode the resolved address (full structured data)
                    latitude, longitude = None, None
                    try:
                        geo_result = self.geocoder.geocode_full(geocode_text)
                        if geo_result:
                            latitude = geo_result['latitude']
                            longitude = geo_result['longitude']
                            logger.debug(f"Geocoded {location_text} -> ({latitude:.6f}, {longitude:.6f})")
                        else:
                            # Fallback to simple geocode (cache-only hits without structured data)
                            latitude, longitude = self.geocoder.geocode(geocode_text)
                            if latitude and longitude:
                                logger.debug(f"Geocoded (fallback) {location_text} -> ({latitude:.6f}, {longitude:.6f})")
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

                    # Insert location record with device_type
                    cursor.execute('''
                        INSERT INTO swift_locations
                        (device_name, location, time_status, distance, latitude, longitude,
                         device_type, raw_data, extracted_at, location_timestamp,
                         distance_from_home_km, battery_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        device_name,
                        location_text,
                        cleaned['timeStatus'],
                        cleaned['distance'],
                        latitude,
                        longitude,
                        device_type,
                        json.dumps(device_data),  # Store original raw data for debugging
                        extracted_at,
                        location_timestamp,
                        dist_home,
                        battery_status,
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
                        device_name,
                        device_type,
                        location_text
                    ))

                    saved_count += 1
                    saved_device_names.add(device_name)

                    # Track visit (dwell time) — reuse conn to avoid locking
                    ts = location_timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    try:
                        update_visits(device_name, location_text, latitude, longitude, ts, conn=conn)
                    except Exception as e:
                        logger.warning(f"Visit tracking failed for {device_name}: {e}")

                conn.commit()
                logger.info(f"Saved {saved_count}/{len(devices)} {device_type} updates")
                if saved_count > 0:
                    print(f"           💾 {device_type}: {saved_count} saved")

            except Exception as e:
                logger.error(f"Error saving {device_type} tab: {e}")
                conn.rollback()
                return 0, set()

        return saved_count, saved_device_names

    def _handle_extraction_failure(self, device_type: str) -> None:
        """
        Track consecutive failures and restart Find My if threshold exceeded.

        Args:
            device_type: Type of tab that failed extraction
        """
        self.consecutive_failures += 1
        logger.warning(
            f"[FAILURE] Extraction failure {self.consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES} "
            f"for {device_type} tab"
        )

        if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            # Check cooldown to avoid restart loops
            if self.last_findmy_restart:
                seconds_since_restart = (datetime.now() - self.last_findmy_restart).total_seconds()
                if seconds_since_restart < self.FINDMY_RESTART_COOLDOWN:
                    logger.warning(
                        f"[RECOVERY] Skipping Find My restart (cooldown: {int(self.FINDMY_RESTART_COOLDOWN - seconds_since_restart)}s remaining)"
                    )
                    return

            logger.error(
                f"[RECOVERY] {self.MAX_CONSECUTIVE_FAILURES} consecutive failures, restarting Find My..."
            )
            self._restart_find_my()
            self.consecutive_failures = 0

    def _try_fix_findmy_window(self) -> bool:
        """
        Call fix_findmy_window.sh to recover from dialog/window issues.

        This script handles:
        - Find My running with 0 windows (blocked by dialog)
        - AppleScript returning empty results
        - Onboarding/What's New dialogs

        Returns:
            True if fix script ran successfully, False otherwise
        """
        if not self.FIX_FINDMY_SCRIPT.exists():
            logger.warning(f"[FIX] Script not found: {self.FIX_FINDMY_SCRIPT}")
            return False

        try:
            logger.info("[FIX] Running fix_findmy_window.sh to recover window state...")
            result = subprocess.run(
                ['bash', str(self.FIX_FINDMY_SCRIPT)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                logger.info("[FIX] Fix script completed successfully")
                time.sleep(2)  # Give Find My time to stabilize
                return True
            else:
                logger.warning(f"[FIX] Fix script returned {result.returncode}")
                if result.stderr:
                    logger.warning(f"[FIX] stderr: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("[FIX] Fix script timed out")
            return False
        except Exception as e:
            logger.error(f"[FIX] Failed to run fix script: {e}")
            return False

    def _restart_find_my(self) -> None:
        """Force quit and relaunch Find My app."""
        logger.info("[RECOVERY] Killing Find My app...")
        print("           ⚠️  Restarting Find My (too many failures)")

        try:
            subprocess.run(['pkill', '-9', 'FindMy'], capture_output=True)
            time.sleep(2)

            logger.info("[RECOVERY] Relaunching Find My...")
            self.automation.ensure_find_my_running()

            # Extra time for iCloud sync after restart
            logger.info("[RECOVERY] Waiting 15s for iCloud sync...")
            time.sleep(15)

            self.last_findmy_restart = datetime.now()
            logger.info("[RECOVERY] Find My restarted successfully")
        except Exception as e:
            logger.error(f"[RECOVERY] Failed to restart Find My: {e}")

    def _reset_failure_count(self) -> None:
        """Reset consecutive failure count after successful extraction."""
        if self.consecutive_failures > 0:
            logger.debug(f"Reset failure count (was {self.consecutive_failures})")
            self.consecutive_failures = 0

    def _maybe_keepalive(self) -> None:
        """
        Periodically refresh Find My to prevent stale state.

        Runs every KEEPALIVE_INTERVAL seconds.
        """
        now = datetime.now()

        # Check if it's time for a preemptive restart (more aggressive)
        if self.last_preemptive_restart is None or \
           (now - self.last_preemptive_restart).total_seconds() >= self.PREEMPTIVE_RESTART_INTERVAL:
            logger.info("[KEEPALIVE] Preemptive Find My restart (every 4h to prevent stale state)")
            print("           🔄 Preemptive Find My restart")
            self._restart_find_my()
            self.last_preemptive_restart = now
            self.last_keepalive = now  # Also counts as keepalive
            return

        # Check if it's time for a simple refresh
        if self.last_keepalive is None or \
           (now - self.last_keepalive).total_seconds() >= self.KEEPALIVE_INTERVAL:
            logger.info("[KEEPALIVE] Sending refresh to Find My")
            self.automation.refresh_find_my()
            self.automation.simulate_mouse_jiggle()
            self.last_keepalive = now

    def _check_temp_wake_expiry(self) -> bool:
        """
        Check if temp wake has expired and restore night mode if needed.

        During a temp wake (triggered by API during night mode), the tracker
        runs for 30 minutes. When this expires, we restore the night mode flag
        and the tracker should exit (watchdog won't restart it).

        Returns:
            True if night mode was restored (tracker should exit)
        """
        if not TEMP_WAKE_FLAG.exists():
            return False

        try:
            expiry_str = TEMP_WAKE_FLAG.read_text().strip()
            expiry = datetime.fromisoformat(expiry_str)

            if datetime.now() >= expiry:
                # Temp wake expired - restore night mode
                logger.info("[TEMP_WAKE] Wake period expired, restoring night mode...")
                print("           🌙 Temp wake expired, going back to sleep")

                # Restore night mode flag
                NIGHT_FLAG.write_text(datetime.now().isoformat())

                # Remove temp wake flag
                TEMP_WAKE_FLAG.unlink(missing_ok=True)

                return True

            else:
                # Temp wake still active
                mins_left = int((expiry - datetime.now()).total_seconds() / 60)
                logger.debug(f"[TEMP_WAKE] Still active, {mins_left} minutes remaining")
                return False

        except (ValueError, OSError) as e:
            logger.warning(f"[TEMP_WAKE] Error checking expiry: {e}")
            return False

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
            # Try immediate fix before giving up
            logger.info(f"[FIX] Attempting immediate recovery for {tab_name} tab...")
            if self._try_fix_findmy_window():
                # Retry after fix
                if not self.automation.ensure_find_my_running():
                    logger.error(f"[FIX] Recovery failed for {tab_name} tab")
                    return False
                logger.info(f"[FIX] Recovery successful, continuing with {tab_name} tab")
            else:
                return False

        self.automation.activate_find_my()

        if not self.automation.switch_to_tab(device_type):
            logger.error(f"Failed to switch to {tab_name} tab")
            # Try immediate fix before giving up
            logger.info(f"[FIX] Attempting immediate recovery for tab switch...")
            if self._try_fix_findmy_window():
                # Retry tab switch after fix
                self.automation.activate_find_my()
                if not self.automation.switch_to_tab(device_type):
                    logger.error(f"[FIX] Tab switch still failing after recovery")
                    return False
                logger.info(f"[FIX] Tab switch recovered successfully")
            else:
                return False

        # Wait for tab to load (Devices tab needs more time than People/Items)
        load_time = self.TAB_LOAD_TIME[device_type]
        logger.info(f"Waiting {load_time}s for {tab_name} tab to load...")
        time.sleep(load_time)

        # Extract locations
        devices = self.extract_locations_for_tab(device_type)

        if not devices:
            logger.warning(f"No {device_type}s found in {tab_name} tab")
            self._handle_extraction_failure(device_type)
            return False

        # Successful extraction - reset failure counter
        self._reset_failure_count()

        # Log summary
        logger.info(f"Found {len(devices)} {device_type}(s):")
        for device in devices:
            status = f"{device['timeStatus']}, {device['distance']}" if device['distance'] != '-' else device['timeStatus']
            logger.info(f"  - {device['name']}: {device['location']} ({status})")

        # Save to database
        saved, device_names = self.save_locations(devices, device_type)

        # Detect trips for each device that had new records (uses resolved names)
        if saved > 0:
            with get_connection() as conn:
                for name in device_names:
                    try:
                        detect_trips(name, since_minutes=10, conn=conn)
                    except Exception as e:
                        logger.warning(f"Trip detection failed for {name}: {e}")
                conn.commit()

        return saved > 0

    def run_single_cycle(self) -> bool:
        """
        Run a single complete cycle through all tabs.

        Returns:
            True if at least one tab was successfully processed
        """
        cycle_start = datetime.now().strftime('%H:%M:%S')
        logger.info("=" * 70)
        logger.info("STARTING NEW TRACKING CYCLE")
        logger.info("=" * 70)
        print(f"\n[{cycle_start}] 🔄 Starting cycle...")

        # Check if keep-alive actions are needed
        self._maybe_keepalive()

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
        cycle_end = datetime.now().strftime('%H:%M:%S')
        logger.info(f"Cycle complete! {success_count}/{len(tabs)} tabs processed successfully")
        logger.info(f"Pausing {self.CYCLE_END_PAUSE}s before next cycle...")
        print(f"[{cycle_end}] ✅ Cycle complete: {success_count}/3 tabs")

        return success_count > 0

    def _maybe_run_retention(self):
        """Run retention aggregation if enough time has passed (1x per hour)."""
        now = datetime.now()
        if not hasattr(self, '_last_retention') or (now - self._last_retention).total_seconds() >= 3600:
            try:
                from retention import run_retention
                logger.info("Running periodic retention aggregation...")
                run_retention(dry_run=False, vacuum=False)
                self._last_retention = now
            except Exception as e:
                logger.warning(f"Retention run failed: {e}")
                self._last_retention = now  # Don't retry immediately

    def run_continuous(self):
        """Run continuous tracking with tab cycling."""
        logger.info("=" * 70)
        logger.info("STARTING ORCHESTRATED AIRTRACKER (CONTINUOUS MODE)")
        logger.info("=" * 70)
        logger.info("Configuration:")
        logger.info(f"  - Initial pause: {self.INITIAL_PAUSE}s")
        logger.info(f"  - Tab load time: People={self.TAB_LOAD_TIME['person']}s, Devices={self.TAB_LOAD_TIME['device']}s, Items={self.TAB_LOAD_TIME['item']}s")
        logger.info(f"  - Extract pause: {self.EXTRACT_PAUSE}s")
        logger.info(f"  - Cycle end pause: {self.CYCLE_END_PAUSE}s")
        logger.info("")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 70)

        try:
            while True:
                # Check if temp wake expired (should go back to night mode)
                if self._check_temp_wake_expiry():
                    logger.info("Temp wake expired, exiting tracker...")
                    break

                self.run_single_cycle()
                self._maybe_run_retention()
                time.sleep(self.CYCLE_END_PAUSE)

        except KeyboardInterrupt:
            logger.info("\n\nOrchestrated tracking stopped by user")

    def run_scheduled(self, interval_minutes: int):
        """Run scheduled tracking with tab cycling."""
        import schedule

        logger.info("=" * 70)
        logger.info("🚀 STARTING ORCHESTRATED AIRTRACKER (SCHEDULED MODE)")
        logger.info("=" * 70)
        logger.info(f"Schedule: Every {interval_minutes} minute(s)")
        logger.info("Configuration:")
        logger.info(f"  - Initial pause: {self.INITIAL_PAUSE}s")
        logger.info(f"  - Tab load time: People={self.TAB_LOAD_TIME['person']}s, Devices={self.TAB_LOAD_TIME['device']}s, Items={self.TAB_LOAD_TIME['item']}s")
        logger.info(f"  - Extract pause: {self.EXTRACT_PAUSE}s")
        logger.info(f"  - Cycle end pause: {self.CYCLE_END_PAUSE}s")
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
                # Check if temp wake expired (should go back to night mode)
                if self._check_temp_wake_expiry():
                    logger.info("Temp wake expired, exiting tracker...")
                    break

                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n\n🛑 Orchestrated tracking stopped by user")


def main():
    """Main entry point for command-line usage."""
    import argparse

    # Ensure only one instance runs at a time
    ensure_singleton()

    parser = argparse.ArgumentParser(
        description="Orchestrated AirTag tracker with automatic tab cycling"
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
        tracker = OrchestratedAirTagTracker()
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
