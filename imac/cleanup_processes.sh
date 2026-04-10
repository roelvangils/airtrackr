#!/bin/bash
# AirTrackr — Clean up unnecessary processes on iMac
#
# Run this after reboot to free up resources and improve stability.
# Can be added to LaunchAgents for automatic execution.

LOG="/Users/evelyn/Repos/airtrackr/logs/tracker.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - cleanup - INFO - $1" >> "$LOG"
    echo "$1"
}

log "[CLEANUP] Starting process cleanup..."

# Kill Safari (uses 500MB+ RAM, not needed)
if pgrep -x Safari > /dev/null; then
    log "[CLEANUP] Quitting Safari..."
    osascript -e 'tell application "Safari" to quit' 2>/dev/null
    sleep 2
    pkill -9 Safari 2>/dev/null
fi

# Kill Activity Monitor (nice to have but uses resources)
if pgrep -f "Activity Monitor" > /dev/null; then
    log "[CLEANUP] Quitting Activity Monitor..."
    osascript -e 'tell application "Activity Monitor" to quit' 2>/dev/null
fi

# Kill Photos/mediaanalysisd (not needed, uses CPU for photo analysis)
pkill -f mediaanalysisd 2>/dev/null
pkill -f photolibraryd 2>/dev/null
pkill -f photoanalysisd 2>/dev/null

# Kill Music/iTunes helper processes
pkill -f AMPLibraryAgent 2>/dev/null
pkill -f Music 2>/dev/null

# Kill News
pkill -f News 2>/dev/null

# Kill Siri
pkill -f Siri 2>/dev/null
pkill -f sirittsd 2>/dev/null

# Reduce Spotlight indexing (heavy disk/CPU)
# Note: Don't fully disable as Find My might need some services
log "[CLEANUP] Reducing Spotlight priority..."
renice 20 $(pgrep -f mds_stores) 2>/dev/null
renice 20 $(pgrep -f mds) 2>/dev/null
renice 20 $(pgrep -f corespotlightd) 2>/dev/null

# Kill TextThumbnailExtension and other QuickLook extensions (not needed headless)
pkill -f ThumbnailExtension 2>/dev/null
pkill -f quicklookd 2>/dev/null

# NOTE: Do NOT kill osascript - the dialog_watcher uses it continuously!
# Old line removed: pkill -f "osascript -e" 2>/dev/null

# Report memory freed
log "[CLEANUP] Process cleanup complete"
log "[CLEANUP] Current memory usage:"
vm_stat | head -5 >> "$LOG"

echo "Done!"
