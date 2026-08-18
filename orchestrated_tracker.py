#!/usr/bin/env python3
"""
Orchestrated AirTag Tracker with Tab Cycling

Cycles through the reachable Find My tabs (People, Devices, Items) to track all
entities in the Find My ecosystem. Me is not reachable on Find My 5.0 — see
run_single_cycle.

Per cycle, for each tab:
1. Ask swift/airtag_extractor to switch to the tab and read it
2. The extractor drives Find My's View menu, waits until the list heading confirms
   the switch, waits for the row count to settle, then returns the rows plus the
   tab name it actually read. The tab button's own selected state is NOT trusted:
   pressing it changes that state without navigating.
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
import logging.handlers
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
EXIT_USER_ACTIVE = 8
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
      'paused' the Mac is in use, so nothing was attempted. Not a failure: retrying
               or restarting Find My would only steal focus from whoever is typing.

    exit_code carries the extractor's last exit code for 'failed', because the right
    response differs sharply: a tab-switch failure is worth retrying, while "no window"
    usually means there is no display to draw one on, which no retry can fix.
    """
    outcome: str
    devices: List[Dict] = field(default_factory=list)
    verified_tab: Optional[str] = None
    detail: Optional[str] = None
    exit_code: Optional[int] = None


# Configure logging - file only to avoid duplicates
# Console shows key events via print(), detailed logs go to file
# Anchored to the repo, not the CWD: launchd gives the process no useful one.
LOG_DIR = REPO_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Rotating, because this process is meant to run unattended for months: at one
# cycle per minute a plain FileHandler writes hundreds of MB per year and nothing
# would ever trim it. 10MB x 3 backups ≈ a few weeks of history, bounded forever.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_DIR / 'tracker.log', maxBytes=10 * 1024 * 1024, backupCount=3)
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
    #
    # Sized so a full cycle (three tabs, details sweeps included) finishes in ~30s,
    # comfortably inside the 1-minute schedule. The old 15s EXTRACT_PAUSE dated from
    # the screenshot era, when nothing verified that the UI had settled; the extractor
    # now confirms the tab switch against the heading and polls the row count, so the
    # long blind pause bought nothing. The scheduler runs cycles on one thread, so an
    # overlong cycle delays the next one rather than stacking on top of it.
    INITIAL_PAUSE = 2      # Initial pause before starting
    EXTRACT_PAUSE = 2      # Pause after extracting data
    CYCLE_END_PAUSE = 60   # Pause at end of cycle before repeating (continuous mode)

    # Handed to the extractor, which waits for the tab switch to be confirmed and
    # then for the row count to stop changing. This replaced a blind per-tab sleep
    # of 15-30s, so a cycle is both faster and no longer able to read the wrong tab.
    EXTRACTOR_WAIT_FOR_TAB = 20   # seconds to wait for the switch to be verified
    EXTRACTOR_SETTLE_MS = 1500    # ms to wait for the row list to stop changing

    # Failure recovery settings
    MAX_CONSECUTIVE_FAILURES = 5
    FINDMY_RESTART_COOLDOWN = 300  # 5 minutes between restarts

    # Restarting Find My cannot conjure a display, and without one it can never build a
    # window — so a "no window" run is not the kind of illness the restart machinery
    # cures. On 2026-08-18 that mistake cost 8 hours: DeskPad (the virtual display) was
    # not running, every cycle exited 4, and the tracker killed and relaunched Find My
    # 33 times to no effect. After this many no-window cycles in a row, say what is
    # actually wrong and stop restarting.
    NO_WINDOW_STREAK_BEFORE_BACKOFF = 3

    # Keep-alive settings
    KEEPALIVE_INTERVAL = 1800  # 30 minutes - force refresh Find My
    PREEMPTIVE_RESTART_INTERVAL = 14400  # 4 hours - restart Find My to prevent stale state

    # Reading Find My means bringing it to the front, which is intolerable while
    # someone is using the Mac. Nothing that steals focus runs until the Mac has been
    # idle this long; any input at all puts the tracker back to sleep immediately.
    # 0 disables the courtesy entirely. Override in config.json:
    #   "automation": { "resume_after_idle_seconds": 300 }
    RESUME_AFTER_IDLE_SECONDS = 300

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
        # Consecutive tabs that came back "no window"; see NO_WINDOW_STREAK_BEFORE_BACKOFF
        self.no_window_streak = 0
        self.display_helper = REPO_DIR / "swift" / "set_display_mode"

        # Keep-alive tracking
        self.last_keepalive: Optional[datetime] = None
        self.last_preemptive_restart: Optional[datetime] = None

        # Set when a cycle stood down because someone is using the Mac. Like
        # fatal_error it ends the cycle, but it is not a failure and must never
        # count toward the Find My restart logic.
        self.paused_for_user: Optional[str] = None
        self.idle_threshold = self._load_idle_threshold()

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

    def _load_idle_threshold(self) -> float:
        """
        Seconds of idle required before the tracker will steal focus.

        config.json wins over the class default. Read through an absolute path: the
        old relative open() quietly fell back to the default whenever the process was
        started from another directory.
        """
        threshold = float(self.RESUME_AFTER_IDLE_SECONDS)
        try:
            with open(REPO_DIR / 'config.json') as f:
                configured = json.load(f).get('automation', {}).get('resume_after_idle_seconds')
            if configured is not None:
                threshold = max(float(configured), 0.0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            logger.debug(f"Using default idle threshold ({threshold}s): {e}")

        try:
            with open(REPO_DIR / 'config.json') as f:
                self.read_details = bool(json.load(f).get('automation', {}).get('read_details', True))
        except (OSError, ValueError, json.JSONDecodeError):
            self.read_details = True

        if threshold > 0:
            logger.info(f"Will pause while the Mac is in use, resuming after {threshold:.0f}s idle")
        else:
            logger.info("User-activity pausing is disabled; Find My will be pulled to the front "
                        "even while the Mac is in use")
        return threshold

    def _request_accessibility_once(self) -> None:
        """
        Ask macOS for Accessibility permission, once per process.

        A LaunchAgent is refused in silence: nothing prompts, and nothing appears in
        System Settings to switch on, so the tracker looks broken with no way forward.
        Asking from inside this process tree registers it in the Accessibility list —
        after which granting it is one toggle rather than hunting for a binary path.
        """
        if getattr(self, '_asked_for_accessibility', False):
            return
        self._asked_for_accessibility = True
        try:
            subprocess.run([str(self.swift_extractor), '--request-permission'],
                           capture_output=True, text=True, timeout=30)
        except subprocess.SubprocessError as e:
            logger.debug(f"Could not request Accessibility permission: {e}")
        logger.error(
            "Grant Accessibility to this tracker: System Settings > Privacy & Security > "
            "Accessibility. It should now be listed (as Python); switch it on. If it is "
            "not listed, add %s with the + button.", self.swift_extractor.parent.parent / 'venv/bin/python'
        )

    def user_idle_seconds(self) -> Optional[float]:
        """
        Seconds since the last user input, or None if it could not be determined.

        Delegates to the extractor so Python and Swift agree on what counts as
        activity — notably that input arriving over Screen Sharing counts.
        """
        try:
            result = subprocess.run(
                [str(self.swift_extractor), '--print-idle'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except (subprocess.SubprocessError, ValueError) as e:
            logger.debug(f"Could not read idle time: {e}")
        return None

    def _user_is_active(self) -> Optional[float]:
        """
        Idle seconds if the Mac is in use and automation should stand down, else None.

        Deliberately fails open: if the idle time cannot be read we go ahead, because
        the extractor re-checks with --require-idle before it touches anything. This
        check exists only to skip the disruptive work that happens *before* the
        extractor runs, such as launching Find My.
        """
        if self.idle_threshold <= 0:
            return None
        idle = self.user_idle_seconds()
        if idle is not None and idle < self.idle_threshold:
            return idle
        return None

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
        # The extractor is the authoritative gate: it re-checks idle time immediately
        # before it activates Find My, catching input that arrives after we looked.
        if self.idle_threshold > 0:
            cmd += ['--require-idle', str(self.idle_threshold)]
        # Street addresses: the plain list only says "Ghent"; selecting each row (which
        # --details does) exposes "Kortrijksesteenweg, Ghent". Costs ~0.3s per row.
        # Disable with automation.read_details=false in config.json.
        if self.read_details:
            cmd += ['--details']

        last_code: Optional[int] = None
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
            last_code = code
            payload = {}
            if result.stdout.strip():
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse extractor JSON for {device_type}: {e}")

            detail = (payload.get('error') or {}).get('message') or result.stderr.strip()

            if code == EXIT_OK:
                # The extractor reports non-fatal trouble (a row that would not enrich,
                # a detail view it had to dismiss) on stderr while still exiting 0.
                # Silently dropping that made the details sweep undebuggable.
                for line in result.stderr.strip().splitlines():
                    if line and not line.startswith('extracted '):
                        logger.warning(f"extractor: {line}")
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

            if code == EXIT_USER_ACTIVE:
                # Someone is using the Mac. The extractor stood down without touching
                # Find My; retrying now would only fight them for focus.
                logger.info(f"Standing down for {device_type} tab: {detail}")
                return ExtractionResult('paused', [], None, detail)

            if code == EXIT_AX_NOT_TRUSTED:
                # Retrying cannot fix a missing TCC grant, and restarting Find My
                # certainly cannot. Surface it and stop the cycle.
                logger.error(f"Accessibility permission missing: {detail}")
                self._request_accessibility_once()
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
        return ExtractionResult('failed', [], None, exit_code=last_code)

    def _geocode(self, geocode_text: str) -> tuple:
        """
        Coordinates for one already alias-resolved location, or (None, None).

        Must be called outside any write transaction on the database — see
        save_locations for what happens otherwise.
        """
        try:
            geo_result = self.geocoder.geocode_full(geocode_text)
            if geo_result:
                logger.debug(f"Geocoded {geocode_text} -> "
                             f"({geo_result['latitude']:.6f}, {geo_result['longitude']:.6f})")
                return geo_result['latitude'], geo_result['longitude']
            # Fallback to simple geocode (cache-only hits without structured data)
            latitude, longitude = self.geocoder.geocode(geocode_text)
            if latitude and longitude:
                logger.debug(f"Geocoded (fallback) {geocode_text} -> ({latitude:.6f}, {longitude:.6f})")
            return latitude, longitude
        except Exception as e:
            logger.warning(f"Geocoding failed for '{geocode_text}': {e}")
            return None, None

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

        # Geocode BEFORE opening the write transaction below, never inside it.
        #
        # The geocoder caches into this same SQLite file on its own connection. Doing
        # that while this function holds a write transaction deadlocks us against
        # ourselves: the cache INSERT waits for a lock we are holding, gives up after
        # busy_timeout, and logs "Failed to save to cache: database is locked". WAL and
        # busy_timeout cannot help — the holder is waiting for the waiter. The effect was
        # that geocoding results were never cached at all (31 failures on 2026-08-18
        # alone), so every cycle re-queried Nominatim for the same place names at 1.1s
        # each, for nothing.
        geocoded: Dict[str, tuple] = {}
        for device_data in devices:
            pre = sanitize_device_data(dict(device_data))
            if pre is None:
                continue
            # The street address (from --details) beats the coarse list label: geocoding
            # "Kortrijksesteenweg, Ghent" lands on the street, "Ghent" on the city centre.
            text = resolve_location_alias(pre.get('address') or pre['location'])
            if text not in geocoded:
                geocoded[text] = self._geocode(text)

        with get_connection() as conn:
            try:
                cursor = conn.cursor()

                for device_data in devices:
                    # Validate: skip rows with no usable or stale location
                    cleaned = sanitize_device_data(dict(device_data))
                    if cleaned is None:
                        logger.debug(f"Skipping {device_data['name']}: no usable location")
                        continue

                    # Parse extracted_at timestamp (Swift outputs UTC with Z suffix).
                    # Stored as UTC like everything else since schema v6 — this used to
                    # convert to local time, which made the column disagree with
                    # `timestamp` and seeded a family of comparison bugs.
                    extracted_at = cleaned.get('extracted_at', '')
                    if extracted_at:
                        try:
                            utc_dt = datetime.fromisoformat(extracted_at.replace('Z', '+00:00'))
                            extracted_at = utc_dt.strftime('%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            # Fallback for unexpected formats
                            extracted_at = extracted_at.replace('T', ' ').replace('Z', '')

                    device_name = resolve_device_alias(cleaned['name'])
                    # Store the street when we have one — the location column has always
                    # held whatever Find My showed, and the selected row simply shows more.
                    location_text = cleaned.get('address') or cleaned['location']

                    # Only write when the device actually moved (or the hourly
                    # heartbeat is due) — this is what keeps a 1-minute cadence from
                    # multiplying the table. The plain label and the street-ness flag
                    # let is_duplicate compare at the precision both rows share, in
                    # both directions; see its docstring.
                    if is_duplicate(conn, device_name, location_text,
                                    coarse_label=cleaned['location'],
                                    has_street=bool(cleaned.get('address'))):
                        logger.debug(f"Skipping duplicate: {device_name} at {location_text}")
                        # Update last_seen in swift_devices, even without a new location record
                        cursor.execute('''
                            UPDATE swift_devices SET last_seen = CURRENT_TIMESTAMP
                            WHERE device_name = ?
                        ''', (device_name,))
                        continue

                    # Resolve alias (e.g. "Home" → "Onderstraat 7, 9000 Ghent") and take
                    # the coordinates from the pre-pass above.
                    geocode_text = resolve_location_alias(location_text)
                    latitude, longitude = geocoded.get(geocode_text, (None, None))

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
                    # UTC like location_timestamp itself — a local fallback here skewed visit
                    # durations by the timezone offset when time_status was unparsed.
                    ts = location_timestamp or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    try:
                        update_visits(device_name, location_text, latitude, longitude, ts, conn=conn)
                    except Exception as e:
                        logger.warning(f"Visit tracking failed for {device_name}: {e}")

                conn.commit()
                logger.info(f"Saved {saved_count}/{len(devices)} {device_type} updates")
                if saved_count > 0:
                    print(f"           💾 {device_type}: {saved_count} saved")

            except Exception:
                # Roll back and RE-RAISE. Returning (0, set()) here made a failed
                # write indistinguishable from "nothing moved", while the rollback
                # also discarded the last_seen updates — so the cycle reported 3/3
                # with a database that had silently absorbed nothing.
                conn.rollback()
                raise
                return 0, set()

        return saved_count, saved_device_names

    def _have_display(self) -> bool:
        """
        Is there a display for Find My to build its window on?

        Fails open (returns True) when it cannot tell, so a broken probe never stops the
        tracker from trying — the extractor's exit 4 remains the real signal.
        """
        if not self.display_helper.exists():
            return True
        try:
            probe = subprocess.run([str(self.display_helper), '--list'],
                                   capture_output=True, text=True, timeout=15)
            return probe.returncode != 1   # 1 == no external display online
        except subprocess.SubprocessError as e:
            logger.debug(f"Display probe failed, assuming a display exists: {e}")
            return True

    def _recover_no_window(self) -> None:
        """
        The one sequence that actually clears a wedged Find My window.

        With the lid shut, the built-in display stays *online but asleep* — and it stays
        the MAIN display, which is where new windows get created. Find My then builds its
        scene on a sleeping screen and its accessibility tree comes up self-referential:
        kAXWindows hands back the AXApplication element, recursively, so there is no
        CardContainerView and every read exits 4.

        Relaunching Find My on its own does NOT clear that — measured on 2026-08-18.
        Declaring user activity does: the sleeping built-in drops offline, the virtual
        display becomes main, and a relaunch then builds a real SceneWindow.

        caffeinate declares activity through a power assertion rather than by posting
        events, so this does not trip the idle gate (verified: the idle counter kept
        climbing straight through it). Never substitute anything that fakes input.
        """
        logger.warning("[RECOVERY] Find My has no usable window; nudging the display "
                       "awake, then relaunching Find My")
        try:
            subprocess.run(['caffeinate', '-u', '-t', '2'], capture_output=True, timeout=15)
        except subprocess.SubprocessError as e:
            logger.debug(f"caffeinate nudge failed: {e}")
        time.sleep(3)
        self._restart_find_my()

    def _diagnose_no_window(self) -> None:
        """
        Explain a persistent "Find My has no accessible window", instead of thrashing.

        Find My is a Catalyst app: it only builds a window scene when the GUI session has
        a display to render on. This Mac usually runs with its lid shut and no monitor, so
        that display is DeskPad's virtual one. No DeskPad, no window, and no amount of
        relaunching Find My changes that.
        """
        deskpad = subprocess.run(['pgrep', '-x', 'DeskPad'],
                                 capture_output=True, text=True).returncode == 0
        displays = "unknown"
        if self.display_helper.exists():
            probe = subprocess.run([str(self.display_helper), '--list'],
                                   capture_output=True, text=True, timeout=15)
            displays = "none online" if probe.returncode == 1 else (
                probe.stdout.strip().splitlines() or ["?"])[0]

        logger.error(
            "[DIAGNOSIS] Find My has had no accessible window for %d tabs in a row. "
            "Restarting it will not help — it cannot build a window without a display. "
            "DeskPad running: %s. External display: %s. "
            "Fix the display (start DeskPad, open the lid, or attach a monitor); "
            "tracking resumes by itself once one exists.",
            self.no_window_streak, "yes" if deskpad else "NO", displays
        )
        if not deskpad:
            logger.error("[DIAGNOSIS] Start it with: open -b com.stengo.DeskPad "
                         "(the com.airtrackr.display agent should be doing this at login "
                         "and every 5 minutes — check logs/display.log)")

    def _handle_extraction_failure(self, device_type: str, exit_code: Optional[int] = None) -> None:
        """
        Track consecutive failures and restart Find My if threshold exceeded.

        Args:
            device_type: Type of tab that failed extraction
            exit_code: The extractor's exit code, so a missing window — which a restart
                cannot fix — is handled by explaining it rather than by relaunching.
        """
        if exit_code == EXIT_NO_WINDOW:
            self.no_window_streak += 1
            # Deliberately never touches consecutive_failures: keeping it below the
            # threshold is what keeps the blind restart machinery out of this.
            #
            # Recovery is retried every 12 no-window tabs (= every ~4 cycles, 3 tabs per
            # cycle), not attempted just once: the display can come back at any moment —
            # DeskPad restarting, a lid opening — and the wedged window it left behind
            # only clears through _recover_no_window. A one-shot attempt made too early
            # would otherwise leave the tracker down until its process restarted.
            if self.no_window_streak % 12 == self.NO_WINDOW_STREAK_BEFORE_BACKOFF:
                self._recover_no_window()
            elif self.no_window_streak > self.NO_WINDOW_STREAK_BEFORE_BACKOFF:
                self._diagnose_no_window()
            return
        else:
            self.no_window_streak = 0

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

        Runs every KEEPALIVE_INTERVAL seconds, but never while the Mac is in use:
        both actions below steal focus, and the restart takes over a minute.
        """
        now = datetime.now()

        # Deliberately does not touch the timestamps, so the refresh happens on the
        # first cycle after the Mac goes idle rather than being skipped entirely.
        idle = self._user_is_active()
        if idle is not None:
            logger.debug(f"Skipping keepalive: Mac in use ({idle:.0f}s since last input)")
            return

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
            # Only if there is a display to rebuild the window on. This restart destroys
            # a working window on purpose, betting that Find My builds a fresh one — a bet
            # it loses badly when no display exists, and the window does not come back
            # until someone intervenes. Skipping is free: the whole point is staleness
            # prevention, and a few more hours of uptime beats an outage.
            if not self._have_display():
                logger.warning("[KEEPALIVE] Skipping preemptive Find My restart: no display "
                               "available to rebuild its window on")
                self.last_preemptive_restart = now
                self.last_keepalive = now
                return
            logger.info("[KEEPALIVE] Preemptive Find My restart (every 4h to prevent stale state)")
            print("           🔄 Preemptive Find My restart")
            self._restart_find_my()
            self.last_preemptive_restart = now
            self.last_keepalive = now  # Also counts as keepalive
            return

        # There used to be a 30-minute "refresh" here that sent Cmd+R to Find My via
        # System Events. It is gone, because it fought the idle gate and lost:
        # the synthetic keystroke IS user input as far as macOS is concerned, so every
        # successful refresh was followed ~5s later by
        #   "Standing down: Mac in use (5s since last input)"
        # and cost 5 minutes of tracking. Measured on 2026-08-18: refreshes at 08:09,
        # 09:01, 10:11, 10:46 and 11:17 each caused exactly that, while the one that
        # failed (09:37, Apple Events timeout) caused no pause at all — the control case.
        #
        # It bought nothing either: Find My refreshes itself, rows routinely read "Now",
        # and under launchd the Apple Event hung half the time anyway. The same trap
        # applies to simulate_mouse_jiggle(). Do not reintroduce either: anything that
        # fakes input will pause this tracker for RESUME_AFTER_IDLE_SECONDS.
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

        # Checked before ensure_find_my_running(), which can launch or reopen Find My
        # and is every bit as disruptive as the tab switch itself.
        idle = self._user_is_active()
        if idle is not None:
            self.paused_for_user = (
                f"Mac in use ({idle:.0f}s since last input); "
                f"resuming after {self.idle_threshold:.0f}s idle"
            )
            logger.info(f"Standing down before {tab_name} tab: {self.paused_for_user}")
            return False

        # Ensure Find My is running. The extractor brings it to the front itself when
        # it needs to change tab, so there is no activate_find_my() call here.
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

        if result.outcome == 'paused':
            # Not a failure: leave the failure counter alone so a long working session
            # can never be mistaken for a broken Find My and trigger a restart.
            self.paused_for_user = result.detail
            return False

        if result.outcome == 'failed':
            logger.warning(f"Extraction failed for {tab_name} tab")
            self._handle_extraction_failure(device_type, result.exit_code)
            return False

        # The tab was read successfully, so Find My is healthy — even if the tab
        # happened to be empty.
        self._reset_failure_count()
        self.no_window_streak = 0

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

        # Save to database. A write failure is a failed tab, not a quiet zero —
        # but it is not Find My's fault either, so it stays out of the
        # consecutive-failure counter that triggers Find My restarts.
        try:
            saved, device_names = self.save_locations(devices, actual_type)
        except Exception as e:
            logger.error(f"Saving {tab_name} tab failed: {e}")
            print(f"           ❌ {tab_name}: opslag mislukt: {e}")
            return False

        # Detect trips for each device that had new records (uses resolved names)
        if saved > 0:
            with get_connection() as conn:
                for name in device_names:
                    try:
                        detect_trips(name, since_minutes=10, conn=conn)
                    except Exception as e:
                        logger.warning(f"Trip detection failed for {name}: {e}")
                conn.commit()

        # A tab where nothing was written is still a SUCCESSFUL tab: with the
        # duplicate check doing its job, "0 saved" is the normal steady state of a
        # world where nothing moved. Freshness is tracked separately — every cycle
        # touches swift_devices.last_seen even when it stores no row, which is what
        # lets the API tell "parked for hours" apart from "scrape broken for hours"
        # (minutes_since_update is computed from last_seen).
        return True

    def run_single_cycle(self) -> bool:
        """
        Run a single complete cycle through all tabs.

        Returns:
            True if at least one tab was successfully processed
        """
        cycle_started = datetime.now()
        cycle_start = cycle_started.strftime('%H:%M:%S')
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
        self.paused_for_user = None
        success_count = 0
        # 'me' is deliberately absent. Find My 5.0 on macOS 27 only exposes tab
        # navigation through its View menu, which has items for People, Devices and
        # Items but none for Me — and pressing the Me tab bar button changes the
        # button's state without navigating, so the tab cannot be reached at all.
        # Requesting it would fail every cycle and drive the Find My restart logic.
        # It carried no rows in the first place. `--tab me` still works manually, and
        # will start succeeding on its own if Apple adds the menu item back.
        tabs = ['person', 'device', 'item']

        for i, device_type in enumerate(tabs):
            # Process the tab
            if self.process_tab(device_type):
                success_count += 1

            if self.fatal_error:
                logger.error(f"Ending cycle early: {self.fatal_error}")
                break

            # Abandon the rest of the cycle the moment the Mac is in use. Carrying on
            # would pull focus away two more times, and the remaining tabs will be read
            # on the next cycle after the Mac goes quiet.
            if self.paused_for_user:
                logger.info(f"Ending cycle early: {self.paused_for_user}")
                break

            # Pause after extraction (except for the last tab, which uses cycle end pause)
            if i < len(tabs) - 1:
                logger.info(f"Pausing {self.EXTRACT_PAUSE}s before next tab...\n")
                time.sleep(self.EXTRACT_PAUSE)

        # End of cycle pause
        cycle_seconds = (datetime.now() - cycle_started).total_seconds()
        cycle_end = datetime.now().strftime('%H:%M:%S')
        if self.paused_for_user:
            logger.info(f"Cycle paused after {success_count}/{len(tabs)} tabs: {self.paused_for_user}")
            print(f"[{cycle_end}] ⏸  Paused — {self.paused_for_user} "
                  f"({success_count}/{len(tabs)} tabs done)")
        else:
            logger.info(f"Cycle complete! {success_count}/{len(tabs)} tabs "
                        f"in {cycle_seconds:.0f}s")
            print(f"[{cycle_end}] ✅ Cycle complete: {success_count}/{len(tabs)} tabs ({cycle_seconds:.0f}s)")
            if cycle_seconds > 55:
                logger.warning(f"Cycle took {cycle_seconds:.0f}s — longer than the 1-minute "
                               "schedule; the next run will start late rather than overlap")
        logger.info(f"Pausing {self.CYCLE_END_PAUSE}s before next cycle...")

        # A pause is not a failed cycle. Reporting False would make run_continuous and
        # the LaunchAgent treat a working-hours pause as a malfunction.
        return success_count > 0 or self.paused_for_user is not None

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
                # Retention has its own hourly gate; without this call it simply never
                # ran — its only other call site is run_continuous, and the LaunchAgent
                # uses --schedule. The table would have grown unbounded on a machine
                # meant to run unattended for months.
                self._maybe_run_retention()
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
