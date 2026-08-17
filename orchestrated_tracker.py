#!/usr/bin/env python3
"""
Orchestrated AirTag Tracker with Tab Cycling

Cycles through every Find My tab (People, Devices, Items, Me) to track all
entities in the Find My ecosystem.

Per cycle, for each tab:
1. Ask swift/airtag_extractor to switch to the tab and read it
2. The extractor presses the tab button, waits until both the tab button's
   selected state and the list heading confirm the switch, waits for the row
   count to settle, then returns the rows plus the tab name it actually read
3. Records are labelled with that verified tab name — never with the tab we asked
   for, so a slow-loading tab can't file its predecessor's rows under the wrong type

Tab switching used to go through AppleScript menu clicks followed by a fixed
15-30s sleep with no verification, which is how devices ended up mislabelled.
"""

import json
import subprocess
import os
import sys
import time
import logging
from dataclasses import dataclass, field
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

REPO_DIR = Path(__file__).resolve().parent

# Exit codes from swift/airtag_extractor — keep in sync with its ExitCode enum.
EXIT_OK = 0
EXIT_AX_NOT_TRUSTED = 2
EXIT_APP_NOT_RUNNING = 3
EXIT_NO_WINDOW = 4
EXIT_TAB_SWITCH_FAILED = 5
EXIT_NO_ROWS = 6
EXIT_AX_ERROR = 7
EXIT_USAGE = 64

# device_type in the database <-> --tab argument <-> the name Find My displays
EXTRACTOR_TAB_ARG = {'person': 'people', 'device': 'devices', 'item': 'items', 'me': 'me'}
TAB_DISPLAY_NAMES = {'person': 'People', 'device': 'Devices', 'item': 'Items', 'me': 'Me'}
DEVICE_TYPE_FOR_TAB = {v: k for k, v in TAB_DISPLAY_NAMES.items()}


@dataclass
class ExtractionResult:
    """
    Outcome of one extractor run.

    outcome is one of:
      'ok'     rows were read from a verified tab
      'empty'  the tab was read and verified but holds no rows (normal for Me)
      'fatal'  something no amount of retrying or restarting will fix
      'failed' transient; counts toward the Find My restart logic
    """
    outcome: str
    devices: List[Dict] = field(default_factory=list)
    verified_tab: Optional[str] = None
    detail: Optional[str] = None


# Configure logging - file only to avoid duplicates
# Console shows key events via print(), detailed logs go to file
# Anchored to the repo, not the CWD: launchd gives the process no useful one.
LOG_DIR = REPO_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_DIR / 'tracker.log')
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
    EXTRACT_PAUSE = 15     # Pause after extracting data
    CYCLE_END_PAUSE = 60   # Pause at end of cycle before repeating

    # Handed to the extractor, which waits for the tab switch to be confirmed and
    # then for the row count to stop changing. This replaced a blind per-tab sleep
    # of 15-30s, so a cycle is both faster and no longer able to read the wrong tab.
    EXTRACTOR_WAIT_FOR_TAB = 20   # seconds to wait for the switch to be verified
    EXTRACTOR_SETTLE_MS = 1500    # ms to wait for the row list to stop changing

    # Failure recovery settings
    MAX_CONSECUTIVE_FAILURES = 5
    FINDMY_RESTART_COOLDOWN = 300  # 5 minutes between restarts

    # Keep-alive settings
    KEEPALIVE_INTERVAL = 1800  # 30 minutes - force refresh Find My
    PREEMPTIVE_RESTART_INTERVAL = 14400  # 4 hours - restart Find My to prevent stale state

    def __init__(self, dry_run: bool = False):
        """Initialize the orchestrated tracker."""
        self.swift_extractor = REPO_DIR / "swift" / "airtag_extractor"
        self.automation = FindMyAutomation()
        self.geocoder = Geocoder()

        # dry_run reads and reports but never writes to the database
        self.dry_run = dry_run
        # Set when a failure no amount of retrying can fix (e.g. missing
        # Accessibility permission); ends the cycle instead of looping on it.
        self.fatal_error: Optional[str] = None

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
        logger.info("Initialized orchestrated tracker%s", " (dry run)" if dry_run else "")

    def extract_locations_for_tab(self, device_type: DeviceType,
                                  retry_count: int = 3) -> 'ExtractionResult':
        """
        Switch to a tab and extract its rows.

        The Swift binary does the tab switch itself and only returns once it has
        confirmed the switch landed, so the tab name it reports back is the tab the
        rows actually came from. Callers must label records with that, not with what
        was requested — see process_tab.

        Args:
            device_type: Which tab to read
            retry_count: Attempts for transient failures

        Returns:
            ExtractionResult with the rows, the verified tab name, and an outcome.
        """
        tab_arg = EXTRACTOR_TAB_ARG[device_type]
        # --launch lets the extractor start Find My, and re-open it when it is
        # running but windowless — a state it lands in often enough that leaving
        # recovery to the retry loop just burns cycles.
        cmd = [
            str(self.swift_extractor),
            '--tab', tab_arg,
            '--wait-for-tab', str(self.EXTRACTOR_WAIT_FOR_TAB),
            '--settle-ms', str(self.EXTRACTOR_SETTLE_MS),
            '--launch',
        ]

        for attempt in range(retry_count):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.EXTRACTOR_WAIT_FOR_TAB + 15,
                )
            except subprocess.TimeoutExpired:
                logger.error(
                    f"Swift extractor timed out for {device_type} tab "
                    f"(attempt {attempt + 1}/{retry_count})"
                )
                if attempt < retry_count - 1:
                    time.sleep(2)
                continue

            code = result.returncode
            payload = {}
            if result.stdout.strip():
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse extractor JSON for {device_type}: {e}")

            detail = (payload.get('error') or {}).get('message') or result.stderr.strip()

            if code == EXIT_OK:
                devices = payload.get('devices', [])
                verified = payload.get('tab')
                # extracted_at is on the envelope, one per run; copy it onto each
                # row so save_locations sees a self-contained record.
                extracted_at = payload.get('extracted_at')
                for device in devices:
                    device.setdefault('extracted_at', extracted_at)
                logger.info(f"Extracted {len(devices)} row(s) from verified tab '{verified}'")
                return ExtractionResult('ok', devices, verified)

            if code == EXIT_NO_ROWS:
                # A healthy read that simply has nothing in it — the Me tab is
                # normally empty. Not a failure; must not trip the restart logic.
                verified = payload.get('tab')
                logger.info(f"Tab '{verified or tab_arg}' is empty (no rows)")
                return ExtractionResult('empty', [], verified)

            if code == EXIT_AX_NOT_TRUSTED:
                # Retrying cannot fix a missing TCC grant, and restarting Find My
                # certainly cannot. Surface it and stop the cycle.
                logger.error(f"Accessibility permission missing: {detail}")
                return ExtractionResult('fatal', [], None, detail)

            if code == EXIT_USAGE:
                logger.error(f"Extractor rejected our arguments (bug): {detail}")
                return ExtractionResult('fatal', [], None, detail)

            if code == EXIT_TAB_SWITCH_FAILED:
                logger.warning(
                    f"Tab switch to {tab_arg} failed "
                    f"(attempt {attempt + 1}/{retry_count}): {detail}"
                )
            else:
                # app_not_running / no_window / ax_error — Find My itself is unwell,
                # which is exactly what the restart-and-fix machinery is for.
                logger.error(
                    f"Extractor exited {code} for {device_type} tab "
                    f"(attempt {attempt + 1}/{retry_count}): {detail}"
                )

            if attempt < retry_count - 1:
                time.sleep(2)

        logger.error(f"Failed to extract {device_type} tab after {retry_count} attempts")
        return ExtractionResult('failed', [], None)

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
                    # Validate: skip rows with no usable or stale location
                    cleaned = sanitize_device_data(dict(device_data))
                    if cleaned is None:
                        logger.debug(f"Skipping {device_data['name']}: no usable location")
                        continue

                    # Parse extracted_at timestamp (Swift outputs UTC with Z suffix)
                    extracted_at = cleaned.get('extracted_at', '')
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
                    battery_status = cleaned.get('battery')

                    # The extractor reports distance as a structured object; the
                    # legacy `distance` TEXT column keeps the rendered form so the
                    # API and dashboard need no changes.
                    distance = cleaned.get('distance') or {}
                    distance_text = distance.get('text')
                    distance_km = distance.get('km')

                    # Insert location record with device_type
                    cursor.execute('''
                        INSERT INTO swift_locations
                        (device_name, location, time_status, distance, latitude, longitude,
                         device_type, raw_data, extracted_at, location_timestamp,
                         distance_from_home_km, battery_status,
                         distance_km, proximity, has_location)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        device_name,
                        location_text,
                        cleaned.get('time_status'),
                        distance_text,
                        latitude,
                        longitude,
                        device_type,
                        json.dumps(device_data),  # Store original raw data for debugging
                        extracted_at,
                        location_timestamp,
                        dist_home,
                        battery_status,
                        distance_km,
                        cleaned.get('proximity'),
                        1 if cleaned.get('has_location') else 0,
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
        Recover a Find My that is running but has no usable window.

        This used to shell out to imac/fix_findmy_window.sh. Re-opening the app by
        bundle id does the same job in one call: Find My only builds its window
        scene when it is brought to the front, so an `open` is what actually
        recreates a missing window.

        Returns:
            True if the reopen command succeeded, False otherwise
        """
        try:
            logger.info("[FIX] Re-opening Find My to restore its window...")
            result = subprocess.run(
                ['open', '-b', 'com.apple.findmy'],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                time.sleep(3)  # Give Find My time to build its scene
                logger.info("[FIX] Re-open succeeded")
                return True
            logger.warning(f"[FIX] Re-open returned {result.returncode}: {result.stderr.strip()}")
            return False
        except subprocess.TimeoutExpired:
            logger.error("[FIX] Re-open timed out")
            return False
        except Exception as e:
            logger.error(f"[FIX] Failed to re-open Find My: {e}")
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

        # A just-started tracker has no stale Find My state to clear, so treat
        # startup as the last restart. Otherwise every run — including every
        # --single-cycle and --dry-run — began by killing and relaunching Find My,
        # adding well over a minute for no benefit.
        if self.last_preemptive_restart is None:
            self.last_preemptive_restart = now
            self.last_keepalive = now
            return

        # Check if it's time for a preemptive restart (more aggressive)
        if (now - self.last_preemptive_restart).total_seconds() >= self.PREEMPTIVE_RESTART_INTERVAL:
            logger.info("[KEEPALIVE] Preemptive Find My restart (every 4h to prevent stale state)")
            print("           🔄 Preemptive Find My restart")
            self._restart_find_my()
            self.last_preemptive_restart = now
            self.last_keepalive = now  # Also counts as keepalive
            return

        # Check if it's time for a simple refresh
        if (now - self.last_keepalive).total_seconds() >= self.KEEPALIVE_INTERVAL:
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
        tab_name = TAB_DISPLAY_NAMES[device_type]

        logger.info(f"{'='*60}")
        logger.info(f"Processing {tab_name} tab...")
        logger.info(f"{'='*60}")

        # Ensure Find My is running. Tab switching itself no longer needs the app
        # to be frontmost — the extractor sends AXPress directly — so there is no
        # activate_find_my() call here any more.
        if not self.automation.ensure_find_my_running():
            logger.error(f"Failed to ensure Find My is running for {tab_name} tab")
            logger.info(f"[FIX] Attempting immediate recovery for {tab_name} tab...")
            if self._try_fix_findmy_window():
                if not self.automation.ensure_find_my_running():
                    logger.error(f"[FIX] Recovery failed for {tab_name} tab")
                    return False
                logger.info(f"[FIX] Recovery successful, continuing with {tab_name} tab")
            else:
                return False

        # Switch + extract in one step; the extractor verifies the switch landed.
        result = self.extract_locations_for_tab(device_type)

        if result.outcome == 'fatal':
            logger.error(f"Aborting {tab_name} tab: {result.detail}")
            print(f"           ❌ {tab_name}: {result.detail}")
            self.fatal_error = result.detail
            return False

        if result.outcome == 'failed':
            logger.warning(f"Extraction failed for {tab_name} tab")
            self._handle_extraction_failure(device_type)
            return False

        # The tab was read successfully, so Find My is healthy — even if the tab
        # happened to be empty.
        self._reset_failure_count()

        if result.outcome == 'empty':
            logger.info(f"No rows in {tab_name} tab")
            return False

        # Label records with the tab the extractor confirmed it read, not the one we
        # asked for. If Find My ever hands us a tab we didn't request, storing it
        # under the requested type would silently corrupt device_type.
        actual_type = DEVICE_TYPE_FOR_TAB.get(result.verified_tab, device_type)
        if actual_type != device_type:
            logger.warning(
                f"Requested {tab_name} but extractor verified '{result.verified_tab}'; "
                f"storing rows as '{actual_type}'"
            )

        devices = result.devices

        # Log summary
        logger.info(f"Found {len(devices)} {actual_type}(s):")
        for device in devices:
            distance = (device.get('distance') or {}).get('text')
            time_status = device.get('time_status') or '-'
            status = f"{time_status}, {distance}" if distance else time_status
            logger.info(f"  - {device['name']}: {device.get('location') or 'no location'} ({status})")

        if self.dry_run:
            print(f"           🔍 {tab_name}: {len(devices)} row(s) read, nothing written (dry run)")
            for device in devices:
                print(f"              {device['name']:32} {device.get('location') or '-':24} "
                      f"{device.get('time_status') or '-'}")
            return True

        # Save to database
        saved, device_names = self.save_locations(devices, actual_type)

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

        self.fatal_error = None
        success_count = 0
        tabs = ['person', 'device', 'item', 'me']

        for i, device_type in enumerate(tabs):
            # Process the tab
            if self.process_tab(device_type):
                success_count += 1

            if self.fatal_error:
                logger.error(f"Ending cycle early: {self.fatal_error}")
                break

            # Pause after extraction (except for the last tab, which uses cycle end pause)
            if i < len(tabs) - 1:
                logger.info(f"Pausing {self.EXTRACT_PAUSE}s before next tab...\n")
                time.sleep(self.EXTRACT_PAUSE)

        # End of cycle pause
        cycle_end = datetime.now().strftime('%H:%M:%S')
        logger.info(f"Cycle complete! {success_count}/{len(tabs)} tabs processed successfully")
        logger.info(f"Pausing {self.CYCLE_END_PAUSE}s before next cycle...")
        print(f"[{cycle_end}] ✅ Cycle complete: {success_count}/{len(tabs)} tabs")

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
        logger.info(f"  - Tab switch wait: {self.EXTRACTOR_WAIT_FOR_TAB}s, settle: {self.EXTRACTOR_SETTLE_MS}ms")
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
        logger.info(f"  - Tab switch wait: {self.EXTRACTOR_WAIT_FOR_TAB}s, settle: {self.EXTRACTOR_SETTLE_MS}ms")
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
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Read and report what would be stored, without writing to the database'
    )

    args = parser.parse_args()

    # A dry run is for inspecting one pass, so it implies --single-cycle.
    if args.dry_run:
        args.single_cycle = True

    # Initialize tracker
    try:
        tracker = OrchestratedAirTagTracker(dry_run=args.dry_run)
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
