#!/bin/bash
# Fix Find My window - dismisses dialogs and sets onboarding flag
#
# This script ensures Find My has a visible window by:
# 1. Starting Find My if not running
# 2. Detecting if window count is 0 or empty (AppleScript failure)
# 3. Pressing Enter to dismiss any blocking dialog
# 4. Setting onboarding flags to prevent future dialogs

LOG="/Users/evelyn/Repos/airtrackr/logs/tracker.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - findmy-fix - INFO - $1" >> "$LOG"
}

# Make sure Find My is running
if ! pgrep -f "FindMy.app" > /dev/null; then
    log "[FINDMY] Starting Find My..."
    open /System/Applications/FindMy.app
    sleep 8
fi

# Check window count
WINDOWS=$(osascript -e 'tell application "System Events" to tell process "FindMy" to return count of windows' 2>/dev/null)

# BUG FIX: Handle both empty string (AppleScript failure) AND "0" (no windows)
if [ -z "$WINDOWS" ] || [ "$WINDOWS" = "0" ]; then
    log "[FINDMY] No window found (got: '${WINDOWS}') - pressing Enter to dismiss dialog..."
    osascript -e "tell application \"FindMy\" to activate"
    sleep 1
    osascript -e "tell application \"System Events\" to key code 36"  # Enter
    sleep 2

    # Set onboarding flags for multiple versions to prevent future dialogs
    log "[FINDMY] Setting onboarding flags..."
    defaults write com.apple.findmy "onboarding_v3.1" -bool true
    defaults write com.apple.findmy "onboarding_v3.2" -bool true
    defaults write com.apple.findmy "onboarding_v4.0" -bool true
    defaults write com.apple.findmy "onboarding_v4.1" -bool true

    # Re-check window count
    sleep 1
    WINDOWS=$(osascript -e 'tell application "System Events" to tell process "FindMy" to return count of windows' 2>/dev/null)
fi

# Final status check
if [ -n "$WINDOWS" ] && [ "$WINDOWS" != "0" ]; then
    log "[FINDMY] OK - Find My has $WINDOWS window(s)"
else
    log "[FINDMY] WARNING: Still no window (got: '${WINDOWS}') - trying Enter again..."
    osascript -e "tell application \"FindMy\" to activate"
    sleep 1
    osascript -e "tell application \"System Events\" to key code 36"
    sleep 2

    # One more check
    WINDOWS=$(osascript -e 'tell application "System Events" to tell process "FindMy" to return count of windows' 2>/dev/null)
    if [ -n "$WINDOWS" ] && [ "$WINDOWS" != "0" ]; then
        log "[FINDMY] OK - Find My recovered with $WINDOWS window(s)"
    else
        log "[FINDMY] ERROR: Could not recover Find My window"
    fi
fi
