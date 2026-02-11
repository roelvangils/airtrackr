#\!/bin/bash
# AirTrackr — Recovery Watchdog
#
# Monitors tracking health and performs escalating recovery:
# - After 20 min of no data: logout/login
# - After 60 min of no data: full reboot (max 4/day)
#
# Safety features:
# - Skips during night mode (00:00-07:00) by checking current hour
# - Never reboots more than once per hour (prevents reboot loops)
# - Stores state in persistent location (survives reboots)
#
# Run via launchd every 5 minutes

WORKDIR="/Users/evelyn/Repos/airtrackr"
DB_PATH="$WORKDIR/database/airtracker.db"
LOG="$WORKDIR/logs/tracker.log"
NIGHT_FLAG="/tmp/airtrackr_night_mode"
# Store reboot count in persistent location (not /tmp which clears on reboot!)
REBOOT_COUNT_FILE="$WORKDIR/logs/watchdog_reboot_count"
LAST_REBOOT_FILE="$WORKDIR/logs/watchdog_last_reboot"
LOGOUT_FLAG="/tmp/airtrackr_logout_pending"

# Night mode hours (tracking is paused, don't trigger recovery)
NIGHT_START=0   # 00:00
NIGHT_END=7     # 07:00

# Thresholds (in minutes)
LOGOUT_THRESHOLD=20
REBOOT_THRESHOLD=60
MAX_REBOOTS_PER_DAY=4
MIN_REBOOT_INTERVAL=60  # Never reboot more than once per hour (failsafe)

log_msg() {
    local LEVEL="$1"
    local MSG="$2"
    local TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    echo "$TIMESTAMP - watchdog - $LEVEL - $MSG" >> "$LOG"
    echo "$TIMESTAMP - $LEVEL - $MSG"
}

# Check night mode by current hour (not flag file, which clears on reboot!)
CURRENT_HOUR=$(date "+%H" | sed 's/^0//')  # Remove leading zero
if [ "$CURRENT_HOUR" -ge "$NIGHT_START" ] && [ "$CURRENT_HOUR" -lt "$NIGHT_END" ]; then
    log_msg "INFO" "[WATCHDOG] Night mode active (hour=$CURRENT_HOUR, window=${NIGHT_START}-${NIGHT_END}), skipping"
    exit 0
fi

# Also check flag file as backup (for temp wake scenarios)
if [ -f "$NIGHT_FLAG" ]; then
    log_msg "INFO" "[WATCHDOG] Night mode flag active, skipping"
    exit 0
fi

# Check if database exists
if [ \! -f "$DB_PATH" ]; then
    log_msg "WARN" "[WATCHDOG] Database not found, skipping"
    exit 0
fi

# Get minutes since last extraction using SQLite (more reliable)
get_minutes_since_extraction() {
    # SQLite calculates the difference directly
    local MINUTES=$(sqlite3 "$DB_PATH" "
        SELECT CAST((julianday('now', 'localtime') - julianday(MAX(extracted_at))) * 24 * 60 AS INTEGER)
        FROM swift_locations
    " 2>/dev/null)
    
    if [ -z "$MINUTES" ] || [ "$MINUTES" = "" ]; then
        echo "0"
    else
        echo "$MINUTES"
    fi
}

# Get today's reboot count
get_reboot_count() {
    local TODAY=$(date "+%Y-%m-%d")
    if [ -f "$REBOOT_COUNT_FILE" ]; then
        local STORED_DATE=$(head -1 "$REBOOT_COUNT_FILE" 2>/dev/null)
        if [ "$STORED_DATE" = "$TODAY" ]; then
            tail -1 "$REBOOT_COUNT_FILE" 2>/dev/null || echo "0"
            return
        fi
    fi
    echo "0"
}

# Increment reboot count
increment_reboot_count() {
    local TODAY=$(date "+%Y-%m-%d")
    local COUNT=$(get_reboot_count)
    local NEW_COUNT=$((COUNT + 1))
    echo "$TODAY" > "$REBOOT_COUNT_FILE"
    echo "$NEW_COUNT" >> "$REBOOT_COUNT_FILE"
    echo "$NEW_COUNT"
}

# Perform logout (will auto-login back)
do_logout() {
    log_msg "WARN" "[WATCHDOG] Initiating logout/login recovery..."
    touch "$LOGOUT_FLAG"
    
    # Kill all airtrackr processes first
    pkill -f orchestrated_tracker 2>/dev/null
    pkill -f "FindMy" 2>/dev/null
    sleep 2
    
    # Logout - the auto-login will bring us back
    log_msg "INFO" "[WATCHDOG] Executing logout command..."
    osascript -e 'tell application "System Events" to log out' 2>&1 | while read line; do log_msg "INFO" "[WATCHDOG] osascript: $line"; done
}

# Get minutes since last reboot (failsafe to prevent reboot loops)
get_minutes_since_last_reboot() {
    if [ -f "$LAST_REBOOT_FILE" ]; then
        local LAST_REBOOT=$(cat "$LAST_REBOOT_FILE" 2>/dev/null)
        if [ -n "$LAST_REBOOT" ]; then
            local NOW=$(date +%s)
            local DIFF=$((NOW - LAST_REBOOT))
            echo $((DIFF / 60))
            return
        fi
    fi
    echo "9999"  # No record = allow reboot
}

# Record reboot timestamp
record_reboot_time() {
    date +%s > "$LAST_REBOOT_FILE"
}

# Perform full reboot
do_reboot() {
    local REBOOT_COUNT=$(get_reboot_count)

    # FAILSAFE: Never reboot more than once per hour
    local MINUTES_SINCE_REBOOT=$(get_minutes_since_last_reboot)
    if [ "$MINUTES_SINCE_REBOOT" -lt "$MIN_REBOOT_INTERVAL" ]; then
        log_msg "WARN" "[WATCHDOG] Last reboot was ${MINUTES_SINCE_REBOOT}min ago (< ${MIN_REBOOT_INTERVAL}min), NOT rebooting"
        log_msg "WARN" "[WATCHDOG] Reboot cooldown active - will retry after cooldown expires"
        exit 0
    fi

    if [ "$REBOOT_COUNT" -ge "$MAX_REBOOTS_PER_DAY" ]; then
        log_msg "ERROR" "[WATCHDOG] Max reboots ($MAX_REBOOTS_PER_DAY) reached today, NOT rebooting"
        log_msg "ERROR" "[WATCHDOG] MANUAL INTERVENTION REQUIRED - tracking has been down for over 1 hour"
        # TODO: Send alert notification here
        exit 1
    fi

    # Record reboot time BEFORE rebooting (persists across reboot)
    record_reboot_time

    local NEW_COUNT=$(increment_reboot_count)
    log_msg "WARN" "[WATCHDOG] Initiating FULL REBOOT (reboot $NEW_COUNT of $MAX_REBOOTS_PER_DAY today)..."

    # Sync filesystem and reboot
    sync
    sleep 2
    sudo /sbin/shutdown -r now "AirTrackr watchdog: auto-recovery reboot"
}

# Main logic
MINUTES_STALE=$(get_minutes_since_extraction)
log_msg "INFO" "[WATCHDOG] Minutes since last extraction: $MINUTES_STALE"

if [ "$MINUTES_STALE" -ge "$REBOOT_THRESHOLD" ]; then
    log_msg "ERROR" "[WATCHDOG] Data stale for ${MINUTES_STALE}min (>= ${REBOOT_THRESHOLD}min reboot threshold)"
    do_reboot
elif [ "$MINUTES_STALE" -ge "$LOGOUT_THRESHOLD" ]; then
    # Check if we already tried logout recently (within last 30 min)
    if [ -f "$LOGOUT_FLAG" ]; then
        LOGOUT_AGE_SECONDS=$(( $(date +%s) - $(stat -f %m "$LOGOUT_FLAG" 2>/dev/null || echo "0") ))
        LOGOUT_AGE_MINUTES=$((LOGOUT_AGE_SECONDS / 60))
        if [ "$LOGOUT_AGE_MINUTES" -lt 30 ]; then
            log_msg "WARN" "[WATCHDOG] Logout already attempted ${LOGOUT_AGE_MINUTES}min ago, waiting for reboot threshold"
            exit 0
        fi
        rm -f "$LOGOUT_FLAG"
    fi
    
    log_msg "WARN" "[WATCHDOG] Data stale for ${MINUTES_STALE}min (>= ${LOGOUT_THRESHOLD}min logout threshold)"
    do_logout
else
    # Clear logout flag if we're healthy
    rm -f "$LOGOUT_FLAG" 2>/dev/null
    if [ "$MINUTES_STALE" -gt 5 ]; then
        log_msg "INFO" "[WATCHDOG] Tracking OK (${MINUTES_STALE}min since last extraction)"
    else
        log_msg "INFO" "[WATCHDOG] Tracking healthy"
    fi
fi
