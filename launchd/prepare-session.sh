#!/bin/bash
#
# Put the GUI session into the state the tracker needs, in the order it needs it.
#
# Find My is a Mac Catalyst app and only builds a window scene when the session has a
# display to render on. This Mac normally runs with its lid shut and no monitor, so that
# display is DeskPad's virtual one. Get that wrong and the failure is silent and total:
# on 2026-08-18, DeskPad was not running from 00:00 to 07:17, every extraction exited 4
# ("no accessible window"), and the tracker killed and relaunched Find My 33 times
# without ever getting a window back. Eight hours, no data.
#
# Hence the order below: display first, Find My second. Runs at login and every few
# minutes after, so it also repairs the state if DeskPad or Find My goes away.
#
# Installed as com.airtrackr.display by launchd/install.sh.

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DESKPAD_BUNDLE="com.stengo.DeskPad"
FINDMY_BUNDLE="com.apple.findmy"

# The virtual display comes up at 3840x2160 HiDPI. Nobody looks at this screen, and the
# 2x backing store is pure waste, so drop to a plain 1:1 mode. Kept generous rather than
# tiny on purpose: Find My's window has to fit, and the Accessibility API only exposes
# rows that are actually rendered — too small a display would silently cost devices.
WIDTH=1920
HEIGHT=1200

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# --- 0. keep the logs from growing forever ------------------------------------
# launchd appends to these files for as long as the machine lives; nothing else
# trims them. Truncating in place is safe with append-mode writers (their next
# write lands at the new end of file), unlike moving the file, which they would
# keep writing to by inode. The tracker's own tracker.log rotates itself in
# Python and is skipped here.
for logfile in "$REPO"/logs/*.log; do
    [ -f "$logfile" ] || continue
    case "$logfile" in *tracker.log) continue ;; esac
    size=$(stat -f%z "$logfile" 2>/dev/null || echo 0)
    if [ "$size" -gt 20971520 ]; then   # 20 MB
        tail -c 2097152 "$logfile" > "$logfile.tmp" && cat "$logfile.tmp" > "$logfile" && rm -f "$logfile.tmp"
        log "trimmed $(basename "$logfile") from $((size / 1048576))MB to 2MB"
    fi
done

# --- 1. the virtual display ---------------------------------------------------
if pgrep -x DeskPad >/dev/null 2>&1; then
    log "DeskPad already running"
else
    # Two attempts: right after login, LaunchServices can refuse the first `open`
    # while the session is still assembling itself. The next agent run is 5 minutes
    # away, which is exactly the window we are trying not to lose.
    for attempt in 1 2; do
        log "starting DeskPad (attempt $attempt)"
        open -b "$DESKPAD_BUNDLE" || log "WARNING: open failed for $DESKPAD_BUNDLE"
        for _ in $(seq 1 20); do
            sleep 0.5
            pgrep -x DeskPad >/dev/null 2>&1 && break
        done
        pgrep -x DeskPad >/dev/null 2>&1 && break
        [ "$attempt" = 1 ] && sleep 5
    done
    pgrep -x DeskPad >/dev/null 2>&1 || log "WARNING: DeskPad did not start after 2 attempts"
fi

# --- 2. its resolution -------------------------------------------------------
if [ -x "$REPO/swift/set_display_mode" ]; then
    # Wait for the display itself, not just the process: DeskPad registers it a moment
    # after launch, and setting a mode before then just fails.
    for _ in $(seq 1 20); do
        "$REPO/swift/set_display_mode" --list >/dev/null 2>&1 && break
        sleep 0.5
    done
    # The set itself gets three tries too: right after the display appears, the mode
    # list can be momentarily incomplete and CGCompleteDisplayConfiguration can fail
    # transiently. Exit 0 covers both "set" and "already correct".
    for attempt in 1 2 3; do
        result="$("$REPO/swift/set_display_mode" --width "$WIDTH" --height "$HEIGHT" 2>&1)" && { log "$result"; break; }
        log "resolution attempt $attempt failed: $result"
        [ "$attempt" = 3 ] || sleep 2
    done
else
    log "WARNING: $REPO/swift/set_display_mode missing — run swift/build_universal.sh"
fi

# --- 3. make sure the live display is the one windows land on ----------------
# With the lid shut, the built-in display stays "online" but asleep — and it stays the
# MAIN display, which is where new windows are created. Find My then builds a scene on a
# sleeping screen and its accessibility tree comes up wedged: kAXWindows returns the
# AXApplication element recursively instead of a window, so there is no CardContainerView
# and every read fails with exit 4. Restarting Find My does NOT clear it.
#
# Declaring user activity is what breaks the deadlock: the sleeping built-in drops
# offline and the virtual display becomes main. This does not post input events, so it
# does not trip the tracker's idle gate (verified: the idle counter kept climbing
# straight through it).
caffeinate -u -t 2 2>/dev/null || true
sleep 2

# --- 4. Find My, now that there is a live display to draw it on --------------
if ! pgrep -x FindMy >/dev/null 2>&1; then
    log "starting Find My"
    # `open -b` and not AppleScript: a LaunchAgent has no Automation grant, and Apple
    # Events then hang until their timeout instead of failing.
    open -b "$FINDMY_BUNDLE" || log "WARNING: could not start Find My"
    for _ in $(seq 1 20); do
        sleep 0.5
        pgrep -x FindMy >/dev/null 2>&1 && break
    done
    if pgrep -x FindMy >/dev/null 2>&1; then
        sleep 5   # give it a moment to build its window scene before the tracker looks
    else
        log "WARNING: Find My did not appear within 10s; the next run retries in 5 min"
    fi
fi

# Deliberately no accessibility probe here.
#
# An earlier version ran `airtag_extractor` to check whether Find My's window was usable,
# and misread the result: it treated every exit code except 4 as healthy, so the exit 2
# it actually got after a reboot ("Accessibility permission is not granted") was logged
# as "Find My window OK". A LaunchAgent running a shell script is a different TCC
# identity than the tracker's python, and it has no Accessibility grant of its own.
#
# Detecting and repairing a wedged window needs the Accessibility API, so it belongs to
# the tracker, which has the grant — see _diagnose_no_window in orchestrated_tracker.py.
# Everything this script does needs no special permission at all.
