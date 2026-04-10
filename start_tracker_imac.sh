#!/bin/bash
# AirTrackr iMac Startup
# Arranges windows, opens Console, starts tracker with caffeinate
#
# Usage: paste in Ghostty on the iMac:
#   ~/Repos/airtrackr/start_tracker_imac.sh

WORKDIR="/Users/evelyn/Repos/airtrackr"
ERRLOG="$HOME/Desktop/airtrackr_errors.log"

cd "$WORKDIR"
source venv/bin/activate
mkdir -p logs
touch "$ERRLOG"

echo "[$(date)] Setting up windows and starting tracker..." >> "$ERRLOG"

# Open Console with error log
open -a Console "$ERRLOG"

sleep 1

# Arrange windows: Find My left 50%, Terminal right-top 25%, Console right-bottom 25%
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

    -- Ghostty: right half, top 50%
    tell application "Ghostty" to activate
    delay 0.3
    tell process "Ghostty"
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

    -- Focus back on Ghostty
    tell application "Ghostty" to activate
end tell
APPLESCRIPT

echo ""
echo "=== Windows arranged ==="
echo "  Left 50%:      Find My"
echo "  Right top:     Ghostty (this terminal) - 25%"
echo "  Right bottom:  Console (error log) - 25%"
echo ""
echo "Starting tracker with caffeinate..."
echo "Press Ctrl+C to stop."
echo ""

# Prevent system sleep
caffeinate -s -w $$ &

# Start tracker
exec python3 orchestrated_tracker.py 2>> "$ERRLOG"
