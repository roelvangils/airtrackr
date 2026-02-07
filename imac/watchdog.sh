#!/bin/bash
# AirTrackr — Watchdog (cron fallback)
#
# Checks if the tracker is running and restarts it via Terminal if not.
# This is a safety net in case the Terminal session is accidentally closed.
#
# Install via crontab:
#   crontab -e
#   * * * * * /Users/evelyn/Repos/airtrackr/imac/watchdog.sh >> /Users/evelyn/Repos/airtrackr/logs/watchdog.log 2>&1

WORKDIR="/Users/evelyn/Repos/airtrackr"
NIGHT_FLAG="/tmp/airtrackr_night_mode"

# Don't restart during night mode — the tracker is intentionally paused
if [ -f "$NIGHT_FLAG" ]; then
    exit 0
fi

if ! pgrep -f "orchestrated_tracker.py" > /dev/null 2>&1; then
    echo "[$(date)] Tracker not running — restarting via Terminal"
    osascript -e "
        tell application \"Terminal\"
            activate
            do script \"$WORKDIR/start_all_imac.sh\"
        end tell
    "
else
    # Tracker is running, no action needed
    :
fi
