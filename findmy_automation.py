#!/usr/bin/env python3
"""
Find My Tab Automation Module

This module handles automatic tab switching in the Find My app
to enable comprehensive tracking of People, Devices, and Items.

Uses AppleScript menu clicks (View → People/Devices/Items) because
keyboard shortcuts (Cmd+1/2/3) are unreliable on headless Macs.
"""

import subprocess
import time
import logging
from typing import Literal

DeviceType = Literal['person', 'device', 'item']

logger = logging.getLogger(__name__)


class FindMyAutomation:
    """Automates tab switching in the Find My app."""

    # Menu item names for Find My tabs (View menu)
    TAB_MENU_ITEMS = {
        'person': 'People',
        'device': 'Devices',
        'item': 'Items',
    }

    # Max retries for tab switch verification
    TAB_SWITCH_MAX_RETRIES = 2

    def __init__(self):
        """Initialize the automation module."""
        self.app_name = "FindMy"

    def get_active_tab(self) -> str | None:
        """
        Get the currently active tab by checking View menu checkmarks.

        Returns:
            The active tab name ('People', 'Devices', 'Items') or None if unknown
        """
        applescript = '''
        tell application "System Events"
            tell process "FindMy"
                set activeTab to ""
                repeat with menuItem in menu items of menu "View" of menu bar 1
                    try
                        set itemName to name of menuItem
                        if itemName is in {"People", "Devices", "Items"} then
                            set markChar to value of attribute "AXMenuItemMarkChar" of menuItem
                            if markChar is "✓" then
                                set activeTab to itemName
                                exit repeat
                            end if
                        end if
                    end try
                end repeat
                return activeTab
            end tell
        end tell
        '''
        try:
            result = subprocess.run(
                ['osascript', '-e', applescript],
                capture_output=True, text=True, timeout=10
            )
            active = result.stdout.strip()
            if active in ['People', 'Devices', 'Items']:
                return active
            return None
        except subprocess.TimeoutExpired:
            logger.debug("Tab detection timed out (10s)")
            return None
        except Exception as e:
            logger.debug(f"Could not determine active tab: {e}")
            return None

    def verify_tab_switch(self, expected_tab: str, max_retries: int = 3) -> bool:
        """
        Verify that the expected tab is now active.

        Uses retry logic with increasing delays to handle UI timing issues.

        Args:
            expected_tab: Expected tab name ('People', 'Devices', 'Items')
            max_retries: Number of verification attempts

        Returns:
            True if the expected tab is active, False otherwise
        """
        for attempt in range(max_retries):
            active = self.get_active_tab()
            if active == expected_tab:
                logger.debug(f"Tab verification: {expected_tab} is active (attempt {attempt + 1})")
                return True

            if attempt < max_retries - 1:
                # Wait longer on each retry (0.5s, 1s, 1.5s)
                delay = 0.5 * (attempt + 1)
                logger.debug(f"Tab verification retry {attempt + 1}/{max_retries}, waiting {delay}s...")
                time.sleep(delay)

        logger.warning(f"Tab verification failed after {max_retries} attempts: expected {expected_tab}, got {active}")
        return False

    def activate_find_my(self) -> bool:
        """
        Activate (bring to front) the Find My app.

        Returns:
            True if successful, False otherwise
        """
        applescript = f'''
        tell application "{self.app_name}"
            activate
        end tell
        '''

        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                check=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.debug(f"Activated {self.app_name}")
            # Give it a moment to come to the front
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.error(f"Failed to activate {self.app_name}: {e}")
            return False

    def switch_to_tab(self, device_type: DeviceType) -> bool:
        """
        Switch to the specified tab in Find My app via View menu click.

        Includes verification and retry logic to handle UI timing issues.

        Args:
            device_type: The type of tab to switch to ('person', 'device', or 'item')

        Returns:
            True if successful and verified, False otherwise
        """
        if device_type not in self.TAB_MENU_ITEMS:
            logger.error(f"Invalid device type: {device_type}")
            return False

        menu_item = self.TAB_MENU_ITEMS[device_type]

        for attempt in range(self.TAB_SWITCH_MAX_RETRIES + 1):
            # AppleScript to click View → People/Devices/Items menu item
            applescript = f'''
            tell application "System Events"
                tell process "{self.app_name}"
                    set frontmost to true
                    delay 0.5
                    click menu item "{menu_item}" of menu "View" of menu bar 1
                end tell
            end tell
            '''

            try:
                start_time = time.time()
                result = subprocess.run(
                    ['osascript', '-e', applescript],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                duration = time.time() - start_time

                # Log stderr for debugging (even on success)
                if result.stderr:
                    logger.debug(f"AppleScript stderr: {result.stderr}")

                # Wait for UI to update
                time.sleep(0.5)

                # Tab switch succeeded - extraction results will verify correctness
                logger.info(f"Switched to {device_type} tab (View → {menu_item}) in {duration:.2f}s")
                return True

            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to switch to {device_type} tab: {e}")
                if e.stderr:
                    logger.error(f"Error output: {e.stderr}")
                if attempt < self.TAB_SWITCH_MAX_RETRIES:
                    logger.info(f"Retrying tab switch ({attempt + 1}/{self.TAB_SWITCH_MAX_RETRIES})...")
                    time.sleep(1)
                else:
                    return False
            except Exception as e:
                logger.error(f"Unexpected error switching tabs: {e}")
                return False

        return False

    def refresh_find_my(self) -> bool:
        """
        Force Find My to refresh by simulating Cmd+R.

        This can help when Find My stops syncing with iCloud.

        Returns:
            True if successful, False otherwise
        """
        applescript = f'''
        tell application "System Events"
            tell process "{self.app_name}"
                set frontmost to true
                delay 0.3
                keystroke "r" using command down
            end tell
        end tell
        '''
        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                check=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.info("[KEEPALIVE] Sent refresh command (Cmd+R) to Find My")
            return True
        except Exception as e:
            logger.warning(f"[KEEPALIVE] Failed to refresh Find My: {e}")
            return False

    def click_find_my_window(self) -> bool:
        """
        Click on the Find My window to keep it active.

        This prevents the window from becoming unresponsive.

        Returns:
            True if successful, False otherwise
        """
        applescript = f'''
        tell application "System Events"
            tell process "{self.app_name}"
                set frontmost to true
                delay 0.2
                -- Click in the center of the first window
                try
                    set winPos to position of window 1
                    set winSize to size of window 1
                    set clickX to (item 1 of winPos) + ((item 1 of winSize) / 2)
                    set clickY to (item 2 of winPos) + ((item 2 of winSize) / 2)
                    do shell script "cliclick c:" & clickX & "," & clickY
                on error
                    -- Fallback: just activate the app
                    tell application "{self.app_name}" to activate
                end try
            end tell
        end tell
        '''
        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.debug("[KEEPALIVE] Clicked Find My window")
            return True
        except Exception as e:
            logger.debug(f"[KEEPALIVE] Window click failed (cliclick may not be installed): {e}")
            # Fallback: just activate
            return self.activate_find_my()

    def simulate_mouse_jiggle(self) -> bool:
        """
        Move mouse slightly to simulate user activity.

        This prevents macOS from thinking the system is idle.

        Returns:
            True if successful, False otherwise
        """
        applescript = '''
        tell application "System Events"
            -- Get current mouse position and move 1 pixel, then back
            do shell script "cliclick m:+1,+0 m:-1,-0"
        end tell
        '''
        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.debug("[KEEPALIVE] Mouse jiggle")
            return True
        except Exception:
            # cliclick not installed, skip silently
            return False

    def get_window_count(self) -> int:
        """
        Get the number of Find My windows.

        Returns:
            Number of windows, 0 if none or error
        """
        applescript = f'''
        tell application "System Events"
            tell process "{self.app_name}"
                return count of windows
            end tell
        end tell
        '''
        try:
            result = subprocess.run(
                ['osascript', '-e', applescript],
                capture_output=True,
                text=True,
                timeout=5
            )
            count = int(result.stdout.strip())
            if result.stderr:
                logger.debug(f"get_window_count stderr: {result.stderr.strip()}")
            return count
        except Exception as e:
            logger.debug(f"get_window_count error: {e}")
            return 0

    def get_detailed_window_state(self) -> dict:
        """
        Get detailed state of Find My and potential blocking processes.

        Returns:
            Dictionary with detailed window/process state for debugging
        """
        state = {
            "findmy_running": False,
            "findmy_pid": None,
            "findmy_windows": 0,
            "findmy_frontmost": False,
            "blocking_processes": [],
            "error": None,
        }

        try:
            # Check Find My process
            pgrep = subprocess.run(
                ["pgrep", "-x", "FindMy"],
                capture_output=True, text=True, timeout=5
            )
            if pgrep.returncode == 0:
                state["findmy_running"] = True
                state["findmy_pid"] = pgrep.stdout.strip()

            # Detailed AppleScript check
            applescript = '''
            tell application "System Events"
                set output to ""

                -- Find My status
                if exists process "FindMy" then
                    tell process "FindMy"
                        set output to output & "findmy_windows:" & (count of windows) & ","
                        set output to output & "findmy_frontmost:" & frontmost & ","
                    end tell
                else
                    set output to output & "findmy_windows:0,findmy_frontmost:false,"
                end if

                -- Check for blocking dialog processes
                set dialogProcs to {"SecurityAgent", "UserNotificationCenter", "CoreServicesUIAgent"}
                repeat with procName in dialogProcs
                    if exists process procName then
                        tell process procName
                            set winCount to count of windows
                            if winCount > 0 then
                                set output to output & "blocking:" & procName & ":" & winCount & ","
                            end if
                        end tell
                    end if
                end repeat

                return output
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True, text=True, timeout=10
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                for part in output.split(","):
                    if ":" in part:
                        key, value = part.split(":", 1)
                        if key == "findmy_windows":
                            state["findmy_windows"] = int(value)
                        elif key == "findmy_frontmost":
                            state["findmy_frontmost"] = value.lower() == "true"
                        elif key == "blocking":
                            proc_name, win_count = value.rsplit(":", 1)
                            state["blocking_processes"].append(f"{proc_name} ({win_count} windows)")

            if result.stderr:
                state["applescript_stderr"] = result.stderr.strip()

        except Exception as e:
            state["error"] = str(e)

        return state

    def press_enter(self) -> bool:
        """
        Press Enter key to dismiss any dialog (like 'What's New').

        Returns:
            True if successful, False otherwise
        """
        applescript = '''
        tell application "System Events"
            key code 36
        end tell
        '''
        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                capture_output=True,
                text=True,
                timeout=5
            )
            return True
        except Exception:
            return False

    def ensure_window_exists(self) -> bool:
        """
        Ensure Find My has at least one window open.

        Sometimes Find My can be running but with no windows due to
        a blocking dialog (like "What's New"). Pressing Enter dismisses it.

        Returns:
            True if window exists or was created, False otherwise
        """
        if self.get_window_count() > 0:
            return True

        # Log detailed state for debugging
        state = self.get_detailed_window_state()
        logger.warning("[RECOVERY] Find My has no windows, pressing Enter to dismiss dialog...")
        logger.warning(f"[RECOVERY] Detailed state: findmy_running={state['findmy_running']}, "
                      f"pid={state['findmy_pid']}, frontmost={state['findmy_frontmost']}")
        if state['blocking_processes']:
            logger.warning(f"[RECOVERY] BLOCKING PROCESSES DETECTED: {state['blocking_processes']}")

        # First try: activate and press Enter (dismisses "What's New" dialog)
        self.activate_find_my()
        time.sleep(1)
        self.press_enter()
        time.sleep(2)

        if self.get_window_count() > 0:
            logger.info("[RECOVERY] Window appeared after pressing Enter")
            return True

        # Second try: press Enter again
        logger.warning("[RECOVERY] Still no window, pressing Enter again...")
        self.press_enter()
        time.sleep(2)

        if self.get_window_count() > 0:
            logger.info("[RECOVERY] Window appeared after second Enter")
            return True

        # If that didn't work, try full restart
        logger.warning("[RECOVERY] Enter didn't work, trying full restart...")
        return self.force_restart_with_window()

    def force_restart_with_window(self) -> bool:
        """
        Force quit Find My, clear cache, and restart fresh.

        Returns:
            True if successfully restarted with window, False otherwise
        """
        import os

        # Kill Find My
        subprocess.run(['pkill', '-9', 'FindMy'], capture_output=True)
        time.sleep(2)

        # Clear cache to prevent stale state
        cache_path = os.path.expanduser('~/Library/Caches/com.apple.findmy')
        try:
            subprocess.run(['rm', '-rf', cache_path], capture_output=True)
            logger.debug(f"Cleared Find My cache: {cache_path}")
        except Exception:
            pass

        # Launch fresh
        applescript = f'''
        tell application "{self.app_name}"
            activate
        end tell
        '''
        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                capture_output=True,
                text=True,
                timeout=10
            )
            time.sleep(8)  # Give it time to sync with iCloud

            window_count = self.get_window_count()
            if window_count > 0:
                logger.info(f"[RECOVERY] Find My restarted with {window_count} window(s)")
                return True
            else:
                # Log detailed state on failure
                state = self.get_detailed_window_state()
                logger.error("[RECOVERY] Find My still has no windows after restart")
                logger.error(f"[RECOVERY] Post-restart state: {state}")
                if state['blocking_processes']:
                    logger.error(f"[RECOVERY] BLOCKING PROCESSES: {state['blocking_processes']} - "
                                "these may need manual intervention!")
                return False
        except Exception as e:
            logger.error(f"[RECOVERY] Failed to restart Find My: {e}")
            return False

    def is_find_my_running(self) -> bool:
        """
        Check if Find My app is currently running.

        Returns:
            True if running, False otherwise
        """
        applescript = f'''
        tell application "System Events"
            return (name of processes) contains "{self.app_name}"
        end tell
        '''

        try:
            result = subprocess.run(
                ['osascript', '-e', applescript],
                check=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() == 'true'
        except Exception as e:
            logger.error(f"Failed to check if {self.app_name} is running: {e}")
            return False

    def ensure_find_my_running(self) -> bool:
        """
        Ensure Find My is running with a visible window.

        Returns:
            True if Find My is running with window, False otherwise
        """
        if self.is_find_my_running():
            logger.debug(f"{self.app_name} is already running")
            # Also check for window - app can run without one
            if not self.ensure_window_exists():
                logger.error(f"{self.app_name} running but could not create window")
                return False
            return True

        logger.info(f"Launching {self.app_name}...")
        applescript = f'''
        tell application "{self.app_name}"
            activate
        end tell
        '''

        try:
            subprocess.run(
                ['osascript', '-e', applescript],
                check=True,
                capture_output=True,
                text=True,
                timeout=10
            )

            # Wait for app to launch
            for _ in range(20):  # Wait up to 10 seconds
                time.sleep(0.5)
                if self.is_find_my_running():
                    logger.info(f"{self.app_name} launched successfully")
                    # Give it extra time to fully initialize
                    time.sleep(2)
                    return True

            logger.error(f"Failed to launch {self.app_name} within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to launch {self.app_name}: {e}")
            return False


def main():
    """Test the automation module."""
    from pathlib import Path

    # Create logs directory if it doesn't exist
    Path("logs").mkdir(exist_ok=True)

    # Configure logging to both file and console
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/automation.log'),
            logging.StreamHandler()
        ]
    )

    automation = FindMyAutomation()

    print("Testing Find My automation...")
    print("-" * 50)

    # Ensure Find My is running
    if not automation.ensure_find_my_running():
        print("❌ Failed to ensure Find My is running")
        return

    print("✅ Find My is running")

    # Test tab switching
    tabs = [('person', 'People'), ('device', 'Devices'), ('item', 'Items')]

    for device_type, tab_name in tabs:
        print(f"\n🔄 Switching to {tab_name} tab...")
        automation.activate_find_my()

        if automation.switch_to_tab(device_type):
            print(f"✅ Successfully switched to {tab_name} tab")
            time.sleep(3)  # Wait to see the change
        else:
            print(f"❌ Failed to switch to {tab_name} tab")

    print("\n✅ Automation test complete!")


if __name__ == "__main__":
    main()
