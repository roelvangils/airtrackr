#!/usr/bin/env python3
"""
Debug Context Module for AirTrackr

Provides enhanced logging, system state capture, and correlation IDs
for debugging tracking failures.
"""

import subprocess
import os
import uuid
import logging
from datetime import datetime
from typing import Dict, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class CycleContext:
    """
    Context manager for tracking cycles with correlation IDs.

    Usage:
        with CycleContext() as ctx:
            ctx.log("Starting extraction")
            ctx.log_error("Failed to extract", system_snapshot=True)
    """

    def __init__(self):
        self.cycle_id = uuid.uuid4().hex[:8]
        self.start_time = datetime.now()
        self.events: List[Dict] = []

    def __enter__(self):
        logger.info(f"[cycle:{self.cycle_id}] === CYCLE START ===")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()
        logger.info(f"[cycle:{self.cycle_id}] === CYCLE END (duration: {duration:.1f}s) ===")
        return False

    def log(self, message: str, level: str = "INFO"):
        """Log a message with cycle correlation ID."""
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(f"[cycle:{self.cycle_id}] {message}")
        self.events.append({
            "time": datetime.now().isoformat(),
            "level": level,
            "message": message
        })

    def log_error(self, message: str, system_snapshot: bool = True, extra_context: Optional[Dict] = None):
        """
        Log an error with optional system state snapshot.

        Args:
            message: Error message
            system_snapshot: If True, capture and log system state
            extra_context: Additional context to log
        """
        logger.error(f"[cycle:{self.cycle_id}] {message}")

        if extra_context:
            for key, value in extra_context.items():
                logger.error(f"[cycle:{self.cycle_id}]   {key}: {value}")

        if system_snapshot:
            snapshot = get_system_snapshot()
            logger.error(f"[cycle:{self.cycle_id}] --- SYSTEM SNAPSHOT ---")
            for key, value in snapshot.items():
                logger.error(f"[cycle:{self.cycle_id}]   {key}: {value}")


def get_system_snapshot() -> Dict:
    """
    Capture current system state for debugging.

    Returns:
        Dictionary with system information
    """
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "uptime": _get_uptime(),
        "load_avg": _get_load_average(),
        "memory_pressure": _get_memory_pressure(),
        "findmy_status": _get_findmy_status(),
        "blocking_dialogs": _get_blocking_dialogs(),
        "gui_processes": _get_gui_processes(),
    }
    return snapshot


def _get_uptime() -> str:
    """Get system uptime."""
    try:
        result = subprocess.run(
            ["uptime"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        return f"error: {e}"


def _get_load_average() -> str:
    """Get system load average."""
    try:
        load = os.getloadavg()
        return f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
    except Exception as e:
        return f"error: {e}"


def _get_memory_pressure() -> str:
    """Get memory pressure status."""
    try:
        result = subprocess.run(
            ["memory_pressure"],
            capture_output=True, text=True, timeout=5
        )
        # Extract just the pressure level
        for line in result.stdout.split('\n'):
            if 'System-wide memory free percentage' in line:
                return line.strip()
        return result.stdout.strip()[:100]
    except Exception as e:
        return f"error: {e}"


def _get_findmy_status() -> Dict:
    """Get detailed Find My app status."""
    status = {
        "process_running": False,
        "pid": None,
        "window_count": 0,
        "frontmost": False,
    }

    try:
        # Check if process is running
        result = subprocess.run(
            ["pgrep", "-x", "FindMy"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            status["process_running"] = True
            status["pid"] = result.stdout.strip()

        # Get window count and frontmost status via AppleScript
        applescript = '''
        tell application "System Events"
            if exists process "FindMy" then
                tell process "FindMy"
                    set windowCount to count of windows
                    set isFront to frontmost
                    return (windowCount as text) & "," & (isFront as text)
                end tell
            else
                return "0,false"
            end if
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                status["window_count"] = int(parts[0])
                status["frontmost"] = parts[1].lower() == "true"

        if result.stderr:
            status["applescript_error"] = result.stderr.strip()

    except Exception as e:
        status["error"] = str(e)

    return status


def _get_blocking_dialogs() -> List[str]:
    """
    Check for processes that typically show blocking dialogs.

    Returns:
        List of dialog-related processes currently running
    """
    dialog_processes = [
        "SecurityAgent",
        "UserNotificationCenter",
        "CoreServicesUIAgent",
        "System Preferences",
        "System Settings",
    ]

    found = []
    try:
        applescript = '''
        tell application "System Events"
            set procNames to name of every process whose background only is false
            return procNames as text
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=10
        )
        running_procs = result.stdout.strip()

        for proc in dialog_processes:
            if proc in running_procs:
                # Get window count for this process
                window_script = f'''
                tell application "System Events"
                    if exists process "{proc}" then
                        tell process "{proc}"
                            return count of windows
                        end tell
                    end if
                    return 0
                end tell
                '''
                win_result = subprocess.run(
                    ["osascript", "-e", window_script],
                    capture_output=True, text=True, timeout=5
                )
                window_count = win_result.stdout.strip()
                found.append(f"{proc} (windows: {window_count})")

    except Exception as e:
        found.append(f"error checking dialogs: {e}")

    return found


def _get_gui_processes() -> str:
    """Get list of foreground GUI processes."""
    try:
        applescript = '''
        tell application "System Events"
            set frontApps to name of every process whose frontmost is true
            return frontApps as text
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        return f"error: {e}"


def log_applescript_result(script_name: str, result: subprocess.CompletedProcess, cycle_id: Optional[str] = None):
    """
    Log the full result of an AppleScript execution.

    Args:
        script_name: Name/description of the script
        result: CompletedProcess from subprocess.run
        cycle_id: Optional cycle correlation ID
    """
    prefix = f"[cycle:{cycle_id}] " if cycle_id else ""

    logger.debug(f"{prefix}[AppleScript:{script_name}] returncode={result.returncode}")

    if result.stdout:
        logger.debug(f"{prefix}[AppleScript:{script_name}] stdout: {result.stdout.strip()}")

    if result.stderr:
        logger.warning(f"{prefix}[AppleScript:{script_name}] stderr: {result.stderr.strip()}")


def log_extractor_result(result: subprocess.CompletedProcess, device_type: str, cycle_id: Optional[str] = None):
    """
    Log the full result of a Swift extractor execution.

    Args:
        result: CompletedProcess from subprocess.run
        device_type: Type of extraction (person, device, item)
        cycle_id: Optional cycle correlation ID
    """
    prefix = f"[cycle:{cycle_id}] " if cycle_id else ""

    logger.debug(f"{prefix}[Extractor:{device_type}] returncode={result.returncode}")

    if result.stdout:
        # Log first 500 chars of output for debugging
        output_preview = result.stdout[:500]
        if len(result.stdout) > 500:
            output_preview += f"... ({len(result.stdout)} chars total)"
        logger.debug(f"{prefix}[Extractor:{device_type}] stdout: {output_preview}")

    if result.stderr:
        logger.warning(f"{prefix}[Extractor:{device_type}] stderr: {result.stderr.strip()}")


# Quick test
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    print("Testing debug context...")

    with CycleContext() as ctx:
        ctx.log("Starting test cycle")

        snapshot = get_system_snapshot()
        print("\nSystem Snapshot:")
        for key, value in snapshot.items():
            print(f"  {key}: {value}")

        ctx.log_error("Test error", system_snapshot=True)

    print("\nDone!")
