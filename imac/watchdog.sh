#!/bin/bash
# AirTrackr — Watchdog
#
# Checks if the tracker is running and restarts it directly if not.
# Runs every 5 minutes via cron as a safety net.
#
# Install via crontab:
#   crontab -e
#   */5 * * * * /Users/evelyn/Repos/airtrackr/imac/watchdog.sh >> /Users/evelyn/Repos/airtrackr/logs/watchdog.log 2>&1

WORKDIR="/Users/evelyn/Repos/airtrackr"
NIGHT_FLAG="/tmp/airtrackr_night_mode"
LOG="$WORKDIR/logs/tracker.log"

# Don't restart during night mode — the tracker is intentionally paused
if [ -f "$NIGHT_FLAG" ]; then
    exit 0
fi

# Health check: verify tracker is actually writing data, not just running
check_tracker_health() {
    if ! command -v sqlite3 &> /dev/null; then
        return 0  # Can't check without sqlite3, assume healthy
    fi

    DB_PATH="$WORKDIR/database/airtracker.db"
    if [ ! -f "$DB_PATH" ]; then
        return 0  # No database yet, assume healthy
    fi

    # Get last extracted_at timestamp and check if it's within 10 minutes
    LAST_EXTRACT=$(sqlite3 "$DB_PATH" "SELECT MAX(extracted_at) FROM swift_locations" 2>/dev/null)
    if [ -z "$LAST_EXTRACT" ] || [ "$LAST_EXTRACT" = "" ]; then
        return 0  # No data yet, assume healthy
    fi

    # Convert to epoch and compare (macOS date syntax)
    LAST_TS=$(date -j -f "%Y-%m-%d %H:%M:%S" "$LAST_EXTRACT" "+%s" 2>/dev/null || echo "0")
    NOW_TS=$(date "+%s")

    if [ "$LAST_TS" -gt 0 ]; then
        MINS_AGO=$(( (NOW_TS - LAST_TS) / 60 ))
        if [ "$MINS_AGO" -gt 10 ]; then
            echo "[$(date)] Tracker stale: last extraction was ${MINS_AGO}m ago"
            echo "$(date '+%Y-%m-%d %H:%M:%S') - watchdog - WARN - [WATCHDOG] Tracker stale: no data for ${MINS_AGO} minutes" >> "$LOG"
            return 1  # Unhealthy
        fi
    fi

    return 0  # Healthy
}

# Check if tracker process is running
TRACKER_RUNNING=true
if ! pgrep -f "orchestrated_tracker.py" > /dev/null 2>&1; then
    TRACKER_RUNNING=false
fi

# Check if tracker is healthy (actually writing data)
TRACKER_HEALTHY=true
if $TRACKER_RUNNING && ! check_tracker_health; then
    TRACKER_HEALTHY=false
    echo "[$(date)] Tracker running but stale — killing and restarting"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - watchdog - WARN - [WATCHDOG] Tracker stale, killing process..." >> "$LOG"
    pkill -f "orchestrated_tracker.py" 2>/dev/null
    sleep 2
    TRACKER_RUNNING=false
fi

if ! $TRACKER_RUNNING; then
    echo "[$(date)] Tracker not running — restarting directly"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - watchdog - INFO - [WATCHDOG] Tracker not running, restarting..." >> "$LOG"

    # Ensure Find My has a window first
    "$WORKDIR/imac/fix_findmy_window.sh"

    # Start tracker directly (not via Terminal)
    cd "$WORKDIR"
    source venv/bin/activate
    nohup python orchestrated_tracker.py >> logs/tracker.log 2>&1 &

    sleep 3
    if pgrep -f "orchestrated_tracker.py" > /dev/null 2>&1; then
        echo "[$(date)] Tracker restarted successfully"
        echo "$(date '+%Y-%m-%d %H:%M:%S') - watchdog - INFO - [WATCHDOG] Tracker restarted successfully" >> "$LOG"
    else
        echo "[$(date)] ERROR: Failed to restart tracker"
        echo "$(date '+%Y-%m-%d %H:%M:%S') - watchdog - ERROR - [WATCHDOG] Failed to restart tracker" >> "$LOG"
    fi
fi
