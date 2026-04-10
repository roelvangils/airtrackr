---
name: check-status
description: Check the status of the AirTrackr system (tunnel, API, database, tracker, iCloud) and analyze logs for errors
disable-model-invocation: true
---

# AirTrackr Status Check

Comprehensive health check of all AirTrackr components including log analysis.

## Step 1: Check SSH tunnel on MacBook

```bash
launchctl list | grep airtrackr
```

Verify `com.airtrackr.tunnel-combined` is running (exit code 0 or `-`).

If exit code is 255 or not running, restart it:

```bash
launchctl kickstart -k gui/$(id -u)/com.airtrackr.tunnel-combined 2>/dev/null
sleep 3
launchctl list | grep airtrackr
```

## Step 2: Check API health via tunnel

```bash
API_KEY=$(grep AIRTRACKR_API_KEY ~/.secrets 2>/dev/null | cut -d= -f2)
curl -sL -H "X-API-Key: $API_KEY" http://localhost:8001/health/ | python3 -m json.tool
```

Extract key metrics from the response:
- `status`: should be "healthy"
- `database_connected`: should be true
- `tracker_status`: should be "active" (not "stale")
- `minutes_since_extraction`: should be < 5 for active tracking
- `internet_connected`: should be true
- `icloud_reachable`: should be true
- `permissions_ok`: should be true

## Step 3: Analyze tracker logs for errors

```bash
ssh evelyn@192.168.50.6 '
echo "=== RECENT ERRORS (last 50 lines) ==="
grep -E "(ERROR|BLOCKING|FAIL)" ~/Repos/airtrackr/logs/tracker.log | tail -20

echo ""
echo "=== CYCLE SUCCESS RATE (last 10 cycles) ==="
grep "Cycle complete" ~/Repos/airtrackr/logs/tracker.log | tail -10

echo ""
echo "=== RECOVERY ATTEMPTS ==="
grep -c "\\[RECOVERY\\]" ~/Repos/airtrackr/logs/tracker.log | xargs -I {} echo "Total recovery attempts: {}"
grep "\\[RECOVERY\\]" ~/Repos/airtrackr/logs/tracker.log | tail -5

echo ""
echo "=== BLOCKING PROCESSES DETECTED ==="
grep "BLOCKING PROCESSES" ~/Repos/airtrackr/logs/tracker.log | tail -5 || echo "None detected"

echo ""
echo "=== DIALOG WATCHER ACTIVITY ==="
tail -10 ~/Repos/airtrackr/logs/dialog_watcher.log 2>/dev/null || echo "No dialog watcher logs"

echo ""
echo "=== SINGLETON EVENTS ==="
grep "\\[SINGLETON\\]" ~/Repos/airtrackr/logs/tracker.log | tail -5
'
```

Look for patterns:
- Repeated "Find My has no windows" → Possible blocking dialog or GUI issue
- "BLOCKING PROCESSES DETECTED" → SecurityAgent or other dialog blocking automation
- "0/3 tabs processed" repeatedly → Systemic failure, needs investigation
- Dialog watcher clicks → System was auto-recovering from dialogs

## Step 4: Check process health on iMac

```bash
ssh evelyn@192.168.50.6 '
echo "=== PROCESSES ==="
pgrep -fl orchestrated_tracker && echo "Tracker: running" || echo "Tracker: NOT RUNNING"
pgrep -fl uvicorn && echo "API: running" || echo "API: NOT RUNNING"
pgrep -fl dialog_watcher && echo "Dialog watcher: running" || echo "Dialog watcher: NOT RUNNING"
pgrep -fl black_screen && echo "Black screen: running" || echo "Black screen: NOT RUNNING"
pgrep -fl FindMy && echo "Find My: running" || echo "Find My: NOT RUNNING"
pgrep -fl caffeinate && echo "Caffeinate: running" || echo "Caffeinate: NOT RUNNING"

echo ""
echo "=== FIND MY WINDOW STATE ==="
osascript -e "tell application \"System Events\" to tell process \"FindMy\" to return (count of windows) & \" windows, frontmost: \" & frontmost" 2>&1
'
```

## Step 5: Present results

Present a summary table:

| Component | Status |
|-----------|--------|
| Tunnel | [status from step 1] |
| API | [reachable or not] |
| Database | [connected, X devices, Y locations] |
| Tracker | [active/stale, last update X minutes ago] |
| Find My | [running, X windows] |
| Dialog watcher | [running or not] |
| iCloud | [reachable or not] |
| Permissions | [ok or missing] |

**Log Analysis Summary:**
- Recent errors: [count and types]
- Cycle success rate: [X/10 successful]
- Recovery attempts: [count]
- Blocking dialogs detected: [yes/no]

## Status interpretation

### Healthy system
- Tunnel running (exit 0 or -)
- API status: "healthy"
- Tracker status: "active"
- minutes_since_extraction < 5
- Cycle success: 3/3 tabs
- No blocking processes

### Degraded system
- API status: "degraded"
- Tracker status: "stale"
- minutes_since_extraction > 10
- Repeated 0/3 or 1/3 cycles
- Recovery attempts increasing

If degraded, suggest:
- Check logs for "BLOCKING PROCESSES" - may need `/imac-vnc` to manually dismiss
- `/restart-imac` to reboot and restore services
- `/imac-vnc` to visually inspect the iMac screen

### Unreachable
- Tunnel not running or exit 255
- API not responding

If unreachable:
1. First try restarting the tunnel (step 1)
2. If still unreachable, the iMac may be offline or asleep

## Step 6: Auto-open VNC on problems

If the system is degraded or has issues (tracker stale, API unreachable, permissions problem, repeated failures in logs), automatically open VNC for visual inspection:

```bash
# Only run this if there are problems detected
open "vnc://localhost:5901"
```

Tell the user: "Er is een probleem gedetecteerd. VNC is geopend zodat je de iMac kunt bekijken. Wachtwoord: `airtrackr`"

Suggest checking:
1. Is Find My open en zichtbaar?
2. Toont Find My locaties of een foutmelding?
3. Is er een systeemdialoog (SecurityAgent, firewall prompt)?
4. Draait de tracker in de terminal?
5. Zijn er foutmeldingen in Console?

## Common issues and solutions

### "Find My has no windows" repeatedly
**Cause:** A blocking dialog (SecurityAgent, firewall prompt) is preventing Find My from being visible.
**Solution:** Check dialog_watcher.log - if empty, the watcher may not be running. Use `/imac-vnc` to manually dismiss.

### "BLOCKING PROCESSES: SecurityAgent"
**Cause:** macOS is asking for permission (Accessibility, firewall, etc.)
**Solution:** Dialog watcher should auto-click Allow. If not working, use `/imac-vnc` to manually allow.

### Cycle shows "0/3 tabs processed" but tracker is running
**Cause:** Find My is running but has no windows, or AppleScript automation is failing.
**Solution:** Check if dialog_watcher is running. Consider `/restart-imac` for a fresh start.

### Dialog watcher log is empty
**Cause:** The watcher may have crashed or never started.
**Solution:** Restart it: `ssh evelyn@192.168.50.6 'launchctl kickstart -k gui/$(id -u)/com.airtrackr.dialogwatcher'`
