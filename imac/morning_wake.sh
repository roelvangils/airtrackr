#!/bin/bash
# AirTrackr — Wake display at 07:00
#
# Removes the night mode flag so start_all_imac.sh resumes the tracker.
# Does NOT start a new tracker — the restart loop in start_all_imac.sh handles that.

LOG="/Users/evelyn/Repos/airtrackr/logs/tracker.log"
NIGHT_FLAG="/tmp/airtrackr_night_mode"

# CRITICAL: Remove night flag FIRST, before any other operations.
# This ensures the tracker resumes even if subsequent commands fail.
# (If brightness or fix_findmy_window.sh fails, the tracker should still run.)
echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [MORNING] Removing night mode flag (fail-safe first step)" >> "$LOG"
rm -f "$NIGHT_FLAG"

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [MORNING] Waking display..." >> "$LOG"
caffeinate -u -t 2
sleep 5

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [MORNING] Setting brightness to 0" >> "$LOG"
/usr/local/bin/brightness 0

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [MORNING] Restarting black_screen overlay" >> "$LOG"
pkill -f black_screen 2>/dev/null
sleep 2
launchctl kickstart gui/$(id -u)/com.airtrackr.blackscreen

sleep 3
/usr/local/bin/brightness 0

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [MORNING] Ensuring Find My has a window..." >> "$LOG"
/Users/evelyn/Repos/airtrackr/imac/fix_findmy_window.sh

echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [MORNING] Complete — display on, black_screen active, tracker resuming" >> "$LOG"
