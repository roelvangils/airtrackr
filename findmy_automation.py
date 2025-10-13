#!/usr/bin/env python3
"""
Find My Tab Automation Module

This module handles automatic tab switching in the Find My app
to enable comprehensive tracking of People, Devices, and Items.

Uses AppleScript to send keyboard shortcuts to Find My app:
- Cmd+1: People tab
- Cmd+2: Devices tab
- Cmd+3: Items tab
"""

import subprocess
import time
import logging
from typing import Literal

DeviceType = Literal['person', 'device', 'item']

logger = logging.getLogger(__name__)


class FindMyAutomation:
    """Automates tab switching in the Find My app."""

    # Keyboard shortcuts for Find My tabs
    TAB_SHORTCUTS = {
        'person': '1',  # Cmd+1 for People
        'device': '2',  # Cmd+2 for Devices
        'item': '3',    # Cmd+3 for Items
    }

    def __init__(self):
        """Initialize the automation module."""
        self.app_name = "FindMy"

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
        Switch to the specified tab in Find My app.

        Args:
            device_type: The type of tab to switch to ('person', 'device', or 'item')

        Returns:
            True if successful, False otherwise
        """
        if device_type not in self.TAB_SHORTCUTS:
            logger.error(f"Invalid device type: {device_type}")
            return False

        key = self.TAB_SHORTCUTS[device_type]

        # AppleScript to send Cmd+<key> to Find My
        applescript = f'''
        tell application "System Events"
            tell process "{self.app_name}"
                keystroke "{key}" using command down
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
            logger.info(f"Switched to {device_type} tab (Cmd+{key})")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to switch to {device_type} tab: {e}")
            if e.stderr:
                logger.error(f"Error output: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error switching tabs: {e}")
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
        Ensure Find My is running, launch it if not.

        Returns:
            True if Find My is running (or was successfully launched), False otherwise
        """
        if self.is_find_my_running():
            logger.debug(f"{self.app_name} is already running")
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
