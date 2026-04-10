#!/bin/bash
# AirTrackr Status Display
cd "$(dirname "$0")"

DB_PATH="database/airtracker.db"
NIGHT_FLAG="/tmp/airtrackr_night_mode"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'
OK="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
WARN="${YELLOW}⚠${NC}"

echo -e "${CYAN}${BOLD}"
echo "    _    _     _____               _"
echo "   / \\  (_)_ _|_   _| __ __ _  ___| | ___ __"
echo "  / _ \\ | | '__|| || '__/ _\` |/ __| |/ / '__|"
echo " / ___ \\| | |   | || | | (_| | (__|   <| |"
echo "/_/   \\_\\_|_|   |_||_|  \\__,_|\\___|_|\\_\\_|"
echo -e "${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${BOLD}System${NC}"
echo -e "  Hostname:    $(hostname)"
echo -e "  Uptime:      $(uptime | sed 's/.*up //' | sed 's/,.*//')"
echo -e "  Date:        $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

echo -e "${BOLD}Services${NC}"
if pgrep -f "uvicorn swift_api" > /dev/null 2>&1; then
    API_PID=$(pgrep -f "uvicorn swift_api" | head -1)
    echo -e "  API:         ${OK} Running (PID $API_PID, port 8001)"
else
    echo -e "  API:         ${FAIL} Not running"
fi

if pgrep -f "FindMy.app" > /dev/null 2>&1; then
    FINDMY_PID=$(pgrep -f "FindMy.app" | head -1)
    echo -e "  Find My:     ${OK} Running (PID $FINDMY_PID)"
else
    echo -e "  Find My:     ${FAIL} Not running"
fi

if pgrep -f "caffeinate" > /dev/null 2>&1; then
    echo -e "  Caffeinate:  ${OK} Active (preventing sleep)"
else
    echo -e "  Caffeinate:  ${WARN} Not running"
fi

if pgrep -f "black_screen" > /dev/null 2>&1; then
    echo -e "  Black Screen:${OK} Active"
else
    echo -e "  Black Screen:${WARN} Not running"
fi

if pgrep -f "orchestrated_tracker" > /dev/null 2>&1; then
    TRACKER_PID=$(pgrep -f "orchestrated_tracker" | head -1)
    echo -e "  Tracker:     ${OK} Running (PID $TRACKER_PID)"
else
    echo -e "  Tracker:     ${FAIL} Not running"
fi

if [ -f "$NIGHT_FLAG" ]; then
    echo -e "  Night Mode:  ${YELLOW}ENABLED${NC} (tracker paused)"
else
    echo -e "  Night Mode:  ${GREEN}Off${NC}"
fi
echo ""

echo -e "${BOLD}Database${NC}"
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    echo -e "  Path:        $DB_PATH"
    echo -e "  Size:        $DB_SIZE"

    DEVICE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM swift_devices" 2>/dev/null)
    LOCATION_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM swift_locations" 2>/dev/null)
    SUMMARY_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM location_summaries" 2>/dev/null)
    LAST_UPDATE=$(sqlite3 "$DB_PATH" "SELECT MAX(timestamp) FROM swift_locations" 2>/dev/null)

    echo -e "  Devices:     $DEVICE_COUNT tracked"
    echo -e "  Locations:   $LOCATION_COUNT records"
    echo -e "  Summaries:   $SUMMARY_COUNT aggregated"

    # Calculate time since last update
    if [ -n "$LAST_UPDATE" ]; then
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
else
    echo -e "  ${FAIL} Database not found"
fi
echo ""

echo -e "${BOLD}Recent Extractions${NC}"
sqlite3 "$DB_PATH" "
    SELECT '  ' || device_name || ' | ' || location || ' | ' ||
           CAST((julianday(datetime('now', 'localtime')) - julianday(timestamp)) * 24 * 60 AS INTEGER) || 'm ago'
    FROM swift_locations
    ORDER BY timestamp DESC
    LIMIT 5
" 2>/dev/null
echo ""

echo -e "${BOLD}Ports${NC}"
if lsof -i :8001 > /dev/null 2>&1; then
    echo -e "  8001 (API):  ${OK} Listening"
else
    echo -e "  8001 (API):  ${FAIL} Not listening"
fi
echo ""

echo -e "${BOLD}API Health${NC}"
# Check if API responds (may require API key, so just check if it responds at all)
RESPONSE=$(curl -sL --max-time 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/health 2>/dev/null)
if [ "$RESPONSE" = "200" ]; then
    echo -e "  Status:      ${OK} Healthy (200 OK)"
elif [ "$RESPONSE" = "403" ] || [ "$RESPONSE" = "401" ]; then
    # API is running but requires auth - that's fine for status check
    echo -e "  Status:      ${OK} Running (auth required)"
elif [ -n "$RESPONSE" ] && [ "$RESPONSE" != "000" ]; then
    echo -e "  Status:      ${WARN} HTTP $RESPONSE"
else
    echo -e "  Status:      ${FAIL} Not responding"
fi
echo ""

echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
