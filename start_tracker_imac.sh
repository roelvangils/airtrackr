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

# Arrange windows: Find My left, Ghostty right, Console bottom-right
osascript << 'APPLESCRIPT'
tell application "System Events"
    -- Screen dimensions (logical points for 5K retina = 2560x1440)
    set screenW to 2560
    set screenH to 1440
    set halfW to screenW / 2
    set menuBar to 25

    -- Find My: left half
    tell application "FindMy" to activate
    delay 0.5
    tell process "FindMy"
        try
            set position of window 1 to {0, menuBar}
            set size of window 1 to {halfW, screenH - menuBar}
        end try
    end tell

    -- Ghostty: right half, top 60%
    tell application "Ghostty" to activate
    delay 0.3
    tell process "Ghostty"
        try
            set position of window 1 to {halfW, menuBar}
            set size of window 1 to {halfW, (screenH - menuBar) * 0.6}
        end try
    end tell

    -- Console: right half, bottom 40%
    delay 0.3
    tell process "Console"
        try
            set topOffset to menuBar + ((screenH - menuBar) * 0.6)
            set position of window 1 to {halfW, topOffset}
            set size of window 1 to {halfW, (screenH - menuBar) * 0.4}
        end try
    end tell

    -- Focus back on Ghostty
    tell application "Ghostty" to activate
end tell
APPLESCRIPT

echo ""
echo "=== Windows arranged ==="
echo "  Left:          Find My"
echo "  Right top:     Ghostty (this terminal)"
echo "  Right bottom:  Console (error log)"
echo ""
echo "Starting tracker with caffeinate..."
echo "Press Ctrl+C to stop."
echo ""

# Prevent system sleep
caffeinate -s -w $$ &

# Start tracker
exec python3 orchestrated_tracker.py 2>> "$ERRLOG"
