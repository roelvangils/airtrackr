#!/bin/bash
# One-shot post-reboot verification. Installed by hand as com.airtrackr.verify-once
# for exactly one reboot test on 2026-08-18, and removes itself when done.
#
# Why it exists: the reboot it verifies also kills the Claude session that would
# otherwise do this check, so the evidence of "did everything come back on its own,
# ~6 minutes after boot, with nobody touching the machine?" has to be collected by
# something that survives. This writes that evidence to logs/reboot-verify.log.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/logs/reboot-verify.log"
LABEL="com.airtrackr.verify-once"

# t+6min: login input keeps the idle gate closed for the first ~5 minutes, so any
# earlier snapshot would show a paused tracker and prove nothing.
sleep 360

{
    echo "===== post-reboot verification $(date '+%Y-%m-%d %H:%M:%S') ====="
    echo "boot: $(sysctl -n kern.boottime)"
    echo
    echo "--- agents ---"
    launchctl list | grep airtrackr || echo "NONE LOADED"
    echo
    echo "--- processes ---"
    pgrep -x DeskPad >/dev/null && echo "DeskPad: running" || echo "DeskPad: NOT RUNNING"
    pgrep -x FindMy  >/dev/null && echo "FindMy:  running" || echo "FindMy:  NOT RUNNING"
    echo
    echo "--- display ---"
    "$REPO/swift/set_display_mode" --list 2>&1 | head -2
    echo
    echo "--- idle (two samples, 20s apart; both should be >300 and climbing) ---"
    "$REPO/swift/airtag_extractor" --print-idle 2>&1
    sleep 20
    "$REPO/swift/airtag_extractor" --print-idle 2>&1
    echo
    echo "--- db ---"
    BOOT_EPOCH=$(sysctl -n kern.boottime | sed 's/.*sec = \([0-9]*\).*/\1/')
    BOOT_UTC=$(date -u -r "$BOOT_EPOCH" '+%Y-%m-%d %H:%M:%S')
    echo "rows since boot ($BOOT_UTC UTC): $(sqlite3 "$REPO/database/airtracker.db" "SELECT COUNT(*) FROM swift_locations WHERE timestamp > '$BOOT_UTC';" 2>&1)"
    echo "latest row: $(sqlite3 "$REPO/database/airtracker.db" 'SELECT MAX(timestamp) FROM swift_locations;' 2>&1) UTC"
    echo
    echo "--- tracker.log since boot ---"
    tail -40 "$REPO/logs/tracker.log"
    echo
    echo "--- display.log since boot ---"
    tail -15 "$REPO/logs/display.log"
    echo "===== end ====="
} > "$OUT" 2>&1

# Self-destruct: this was a one-reboot instrument, not a service.
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
