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
DB_PATH="$WORKDIR/database/airtracker.db"

mkdir -p "$LOGDIR"
cd "$WORKDIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Status indicators
OK="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
WARN="${YELLOW}⚠${NC}"

show_status() {
    clear
    echo -e "${CYAN}${BOLD}"
    echo "    _    _     _____               _"
    echo "   / \\  (_)_ _|_   _| __ __ _  ___| | ___ __"
    echo "  / _ \\ | | '__|| || '__/ _\` |/ __| |/ / '__|"
    echo " / ___ \\| | |   | || | | (_| | (__|   <| |"
    echo "/_/   \\_\\_|_|   |_||_|  \\__,_|\\___|_|\\_\\_|"
    echo -e "${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
    echo ""

    # System Info
    echo -e "${BOLD}System${NC}"
    echo -e "  Hostname:    $(hostname)"
    echo -e "  Uptime:      $(uptime | sed 's/.*up //' | sed 's/,.*//')"
    echo -e "  Date:        $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    # Service Status
    echo -e "${BOLD}Services${NC}"

    # API (port 8001)
    if pgrep -f "uvicorn swift_api" > /dev/null 2>&1; then
        API_PID=$(pgrep -f "uvicorn swift_api" | head -1)
        echo -e "  API:         ${OK} Running (PID $API_PID, port 8001)"
    else
        echo -e "  API:         ${FAIL} Not running"
    fi

    # Find My
    if pgrep -f "FindMy.app" > /dev/null 2>&1; then
        FINDMY_PID=$(pgrep -f "FindMy.app" | head -1)
        echo -e "  Find My:     ${OK} Running (PID $FINDMY_PID)"
    else
        echo -e "  Find My:     ${FAIL} Not running"
    fi

    # Caffeinate
    if pgrep -f "caffeinate" > /dev/null 2>&1; then
        echo -e "  Caffeinate:  ${OK} Active (preventing sleep)"
    else
        echo -e "  Caffeinate:  ${WARN} Not running"
    fi

    # Black Screen
    if pgrep -f "black_screen" > /dev/null 2>&1; then
        echo -e "  Black Screen:${OK} Active"
    else
        echo -e "  Black Screen:${WARN} Not running"
    fi

    # Night Mode
    if [ -f "$NIGHT_FLAG" ]; then
        echo -e "  Night Mode:  ${YELLOW}ENABLED${NC} (tracker paused)"
    else
        echo -e "  Night Mode:  ${GREEN}Off${NC}"
    fi
    echo ""

    # Database Status
    echo -e "${BOLD}Database${NC}"
    if [ -f "$DB_PATH" ]; then
        DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
        echo -e "  Path:        $DB_PATH"
        echo -e "  Size:        $DB_SIZE"

        # Query database for stats
        if command -v sqlite3 &> /dev/null; then
            DEVICE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM swift_devices" 2>/dev/null || echo "?")
            LOCATION_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM swift_locations" 2>/dev/null || echo "?")
            LAST_UPDATE=$(sqlite3 "$DB_PATH" "SELECT MAX(extracted_at) FROM swift_locations" 2>/dev/null || echo "?")
            SUMMARY_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM location_summaries" 2>/dev/null || echo "0")

            echo -e "  Devices:     $DEVICE_COUNT tracked"
            echo -e "  Locations:   $LOCATION_COUNT records"
            echo -e "  Summaries:   $SUMMARY_COUNT aggregated"

            if [ "$LAST_UPDATE" != "?" ] && [ -n "$LAST_UPDATE" ]; then
                # Calculate minutes since last update
                LAST_TS=$(date -j -f "%Y-%m-%d %H:%M:%S" "$LAST_UPDATE" "+%s" 2>/dev/null || echo "0")
                NOW_TS=$(date "+%s")
                if [ "$LAST_TS" -gt 0 ]; then
                    MINS_AGO=$(( (NOW_TS - LAST_TS) / 60 ))
                    if [ "$MINS_AGO" -lt 5 ]; then
                        echo -e "  Last Update: ${GREEN}$LAST_UPDATE${NC} (${MINS_AGO}m ago)"
                    elif [ "$MINS_AGO" -lt 30 ]; then
                        echo -e "  Last Update: ${YELLOW}$LAST_UPDATE${NC} (${MINS_AGO}m ago)"
                    else
                        echo -e "  Last Update: ${RED}$LAST_UPDATE${NC} (${MINS_AGO}m ago)"
                    fi
                else
                    echo -e "  Last Update: $LAST_UPDATE"
                fi
            fi

            # Show last 3 extracted devices
            echo ""
            echo -e "${BOLD}Recent Extractions${NC}"
            sqlite3 -separator " | " "$DB_PATH" "
                SELECT device_name, location,
                       CAST((julianday('now') - julianday(timestamp)) * 24 * 60 AS INTEGER) || 'm ago'
                FROM swift_locations
                ORDER BY timestamp DESC
                LIMIT 5
            " 2>/dev/null | while read line; do
                echo -e "  $line"
            done
        else
            echo -e "  ${WARN} sqlite3 not available for detailed stats"
        fi
    else
        echo -e "  ${FAIL} Database not found at $DB_PATH"
    fi
    echo ""

    # Port Status
    echo -e "${BOLD}Ports${NC}"
    if lsof -i :8001 > /dev/null 2>&1; then
        echo -e "  8001 (API):  ${OK} Listening"
    else
        echo -e "  8001 (API):  ${FAIL} Not listening"
    fi
    echo ""

    # API Health Check (if running)
    if pgrep -f "uvicorn swift_api" > /dev/null 2>&1; then
        echo -e "${BOLD}API Health${NC}"
        HEALTH=$(curl -sL --max-time 2 http://127.0.0.1:8001/health 2>/dev/null)
        if [ -n "$HEALTH" ]; then
            STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
            if [ "$STATUS" = "healthy" ]; then
                echo -e "  Status:      ${OK} Healthy"
            else
                echo -e "  Status:      ${FAIL} $STATUS"
            fi
        else
            echo -e "  Status:      ${WARN} Not responding"
        fi
        echo ""
    fi

    echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
}

# Show initial status
show_status

echo "[$(date)] AirTrackr tracker starting..." >> "$ERRLOG"

# Prevent sleep: -d=display, -i=idle, -m=disk, -s=system
# Display must stay on (dimmed by black_screen), network must stay connected
caffeinate -dims -w $$ &

# Ensure black_screen is running (LaunchAgent may have failed)
if ! pgrep -f black_screen > /dev/null 2>&1; then
    echo -e "${YELLOW}Black screen not running, starting...${NC}"
    nohup "$WORKDIR/swift/black_screen" > /dev/null 2>&1 &
    sleep 1
fi

# Ensure VNC uses simple password auth (not macOS login)
echo -e "${BOLD}Configuring VNC...${NC}"
echo "evelyn" | sudo -S /System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart \
    -configure -clientopts -setvnclegacy -vnclegacy yes -setvncpw -vncpw airtrackr > /dev/null 2>&1

# Open Console with tracker log for monitoring
echo -e "${BOLD}Opening Console for log monitoring...${NC}"
open -a Console "$LOGDIR/tracker.log"
sleep 1

# Arrange windows: Find My left 50%, Terminal right-top 25%, Console right-bottom 25%
echo -e "${BOLD}Arranging windows...${NC}"
osascript << 'APPLESCRIPT'
tell application "System Events"
    -- Screen dimensions (logical points for 5K retina = 2560x1440)
    set screenW to 2560
    set screenH to 1440
    set halfW to screenW / 2
    set halfH to (screenH - 25) / 2  -- Account for menu bar
    set menuBar to 25

    -- Find My: left half (50% of screen)
    tell application "FindMy" to activate
    delay 0.5
    tell process "FindMy"
        try
            set position of window 1 to {0, menuBar}
            set size of window 1 to {halfW, screenH - menuBar}
        end try
    end tell

    -- Terminal: right half, top 50%
    delay 0.3
    tell process "Terminal"
        try
            set position of window 1 to {halfW, menuBar}
            set size of window 1 to {halfW, halfH}
        end try
    end tell

    -- Console: right half, bottom 50%
    delay 0.3
    tell process "Console"
        try
            set position of window 1 to {halfW, menuBar + halfH}
            set size of window 1 to {halfW, halfH}
        end try
    end tell

    -- Focus back on Terminal
    tell application "Terminal" to activate
end tell
APPLESCRIPT

echo -e "${GREEN}Windows arranged:${NC}"
echo -e "  ${CYAN}Left 50%:${NC}      Find My"
echo -e "  ${CYAN}Right top:${NC}     Terminal (this window)"
echo -e "  ${CYAN}Right bottom:${NC}  Console (logs)"
echo ""

echo -e "${BOLD}Waiting 45s for Find My iCloud sync...${NC}"
echo ""

# Wait for Find My to open and sync with iCloud (increased from 30s)
for i in $(seq 45 -1 1); do
    printf "\r  Countdown: ${CYAN}%2d${NC}s " "$i"
    sleep 1
done
printf "\r                    \r"

# Verify Find My is running
if pgrep -f "FindMy.app" > /dev/null 2>&1; then
    echo -e "${GREEN}Find My is running${NC}"
else
    echo -e "${YELLOW}Find My not detected, launching...${NC}"
    open -a "Find My"
    sleep 5
fi

# Fix Find My window (dismiss "What's New" dialog if present)
echo -e "${BOLD}Fixing Find My window...${NC}"
"$WORKDIR/imac/fix_findmy_window.sh"

echo "[$(date)] Starting tracker..." >> "$ERRLOG"
echo -e "${GREEN}${BOLD}Launching tracker...${NC}"
echo ""

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
            echo -e "${YELLOW}Night mode active, tracker paused...${NC}"
            sleep 30
            continue
        fi
    fi

    echo "[$(date)] Launching orchestrated_tracker.py" >> "$ERRLOG"
    "$VIRTUAL_ENV/bin/python3" orchestrated_tracker.py 2>> "$LOGDIR/tracker_stderr.log"
    EXIT_CODE=$?
    echo ""

    # Handle different exit codes
    case $EXIT_CODE in
        0)
            echo -e "${YELLOW}Tracker exited normally (code 0)${NC}"
            ;;
        130|143)
            echo -e "${YELLOW}Tracker stopped by signal (code $EXIT_CODE)${NC}"
            ;;
        *)
            echo -e "${RED}Tracker crashed with code $EXIT_CODE${NC}"
            # Check for common issues
            if ! pgrep -f "FindMy.app" > /dev/null 2>&1; then
                echo -e "${YELLOW}Find My not running, restarting...${NC}"
                open -a "Find My"
                sleep 5
            fi
            ;;
    esac

    echo "[$(date)] Tracker exited with code $EXIT_CODE, restarting in 10s..." >> "$ERRLOG"
    echo -e "Restarting in 10s..."
    sleep 10
    echo ""
    show_status
    echo -e "${GREEN}${BOLD}Restarting tracker...${NC}"
    echo ""
done
