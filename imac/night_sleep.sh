#!/bin/bash
# AirTrackr — Put display to sleep during night hours (01:00–07:00)
#
# Runs at 01:00 every night AND at login (RunAtLoad).
# At login, only sleeps the display if current time is in the night window.
# This handles reboots that happen during the night (e.g. Monday 04:00).

LOG="/Users/evelyn/Repos/airtrackr/logs/tracker.log"
NIGHT_FLAG="/tmp/airtrackr_night_mode"
HOUR=$(date +%H)

if [ "$HOUR" -ge 1 ] && [ "$HOUR" -lt 7 ]; then
    sleep 30  # wait for boot/login to settle
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [NIGHT] Creating night mode flag" >> "$LOG"
    touch "$NIGHT_FLAG"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [NIGHT] Stopping tracker..." >> "$LOG"
    pkill -f orchestrated_tracker 2>/dev/null
    sleep 2
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [NIGHT] Putting display to sleep" >> "$LOG"
    pmset displaysleepnow
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [NIGHT] Night mode active — tracker paused, display asleep" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - scheduler - INFO - [NIGHT] Outside night window (hour=$HOUR), skipping" >> "$LOG"
fi
