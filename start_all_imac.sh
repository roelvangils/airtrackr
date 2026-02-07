#!/bin/bash
# AirTrackr — Tracker startup for iMac
# API is managed separately by com.airtrackr.api.plist LaunchAgent.
# This script runs the tracker with a restart loop for crash recovery.
#
# Night mode: when /tmp/airtrackr_night_mode exists, the tracker
# is paused. The file is created by night_sleep.sh (01:00) and
# removed by morning_wake.sh (07:00).
#
# Used by:
#   - AirTrackr.command (Terminal Login Item)
#   - imac/watchdog.sh (cron fallback)

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export VIRTUAL_ENV="/Users/evelyn/Repos/airtrackr/venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR="/Users/evelyn/Repos/airtrackr"
LOGDIR="$WORKDIR/logs"
ERRLOG="$WORKDIR/logs/startup.log"
NIGHT_FLAG="/tmp/airtrackr_night_mode"

mkdir -p "$LOGDIR"
cd "$WORKDIR"

echo "[$(date)] AirTrackr tracker starting..." >> "$ERRLOG"

# Prevent system sleep (screen can turn off)
caffeinate -s -w $$ &

# Wait for Find My to open and sync with iCloud (it auto-starts at login)
sleep 30
echo "[$(date)] Starting tracker..." >> "$ERRLOG"

# Restart loop: if tracker crashes, wait 10s and restart
while true; do
    # Night mode: wait until the flag is removed by morning_wake.sh
    if [ -f "$NIGHT_FLAG" ]; then
        # Safety net: if it's past 07:00 and the flag still exists,
        # morning_wake.sh must have failed — remove the stale flag
        HOUR=$(date +%H)
        if [ "$HOUR" -ge 7 ]; then
            echo "[$(date)] Stale night flag detected (hour=$HOUR), removing" >> "$ERRLOG"
            rm -f "$NIGHT_FLAG"
        else
            sleep 30
            continue
        fi
    fi

    echo "[$(date)] Launching orchestrated_tracker.py" >> "$ERRLOG"
    "$VIRTUAL_ENV/bin/python3" orchestrated_tracker.py 2>> "$LOGDIR/tracker_stderr.log"
    EXIT_CODE=$?
    echo "[$(date)] Tracker exited with code $EXIT_CODE, restarting in 10s..." >> "$ERRLOG"
    sleep 10
done
