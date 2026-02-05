#!/bin/bash
# AirTrackr — Combined startup for iMac (used by LaunchAgent)
# Starts: API server + Tracker + Caffeinate
#
# Install LaunchAgent:
#   cp imac/com.airtrackr.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.airtrackr.plist

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export VIRTUAL_ENV="/Users/evelyn/Repos/airtrackr/venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR="/Users/evelyn/Repos/airtrackr"
LOGDIR="$WORKDIR/logs"
ERRLOG="$HOME/Desktop/airtrackr_errors.log"

mkdir -p "$LOGDIR"
cd "$WORKDIR"

echo "[$(date)] AirTrackr starting..." >> "$ERRLOG"

# Prevent system sleep (screen can turn off)
caffeinate -s -w $$ &

# Start API server in background
"$VIRTUAL_ENV/bin/python3" -m uvicorn swift_api:app --host 192.168.50.6 --port 8001 >> "$LOGDIR/api.log" 2>&1 &
API_PID=$!
echo "[$(date)] API started (PID $API_PID)" >> "$ERRLOG"

# Run tracker in foreground (so launchd can monitor it)
exec "$VIRTUAL_ENV/bin/python3" orchestrated_tracker.py 2>> "$ERRLOG"
