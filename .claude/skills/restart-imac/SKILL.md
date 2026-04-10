---
name: restart-imac
description: Restart the AirTrackr iMac server and verify all services come back up correctly
disable-model-invocation: true
---

# Restart iMac & Full Health Check

Restart the AirTrackr iMac (192.168.50.6, user: evelyn) and verify everything comes back online.

## Step 1: Pre-reboot status

Before rebooting, capture current state for comparison:

```bash
ssh -o ConnectTimeout=10 evelyn@192.168.50.6 "echo '=== PRE-REBOOT STATUS ==='; uptime; echo; launchctl list | grep airtrackr; echo; pgrep -fl orchestrated_tracker; pgrep -fl uvicorn; pgrep -fl black_screen; pgrep -fl FindMy; pgrep -fl caffeinate" 2>&1 || echo "ERROR: Cannot connect to iMac"
```

If SSH fails, stop and report the error to the user.

## Step 2: Reboot

```bash
# Log the reboot initiation to tracker log
ssh evelyn@192.168.50.6 "echo \"\$(date '+%Y-%m-%d %H:%M:%S') - restart-skill - INFO - [REBOOT] Manual reboot initiated via restart-imac skill\" >> ~/Repos/airtrackr/logs/tracker.log"

# Then reboot
ssh evelyn@192.168.50.6 "sudo -S reboot" <<< "evelyn" 2>&1
echo "Reboot command sent"
```

## Step 3: Wait for reboot

Wait 90 seconds for the iMac to reboot and auto-login, then verify SSH is accessible. Retry every 15 seconds up to 5 times.

```bash
echo "Waiting 90 seconds for reboot..."
sleep 90

for i in 1 2 3 4 5; do
  if ssh -o ConnectTimeout=10 evelyn@192.168.50.6 "uptime" 2>&1; then
    echo "SSH connected!"
    break
  else
    echo "Attempt $i: not reachable, waiting 15s..."
    sleep 15
  fi
done
```

## Step 4: Wait for services and Find My sync

Wait 120 seconds for all LaunchAgents, Find My iCloud sync, and tracker initialization. Find My needs extra time to sync location data after reboot.

```bash
echo "Waiting 120 seconds for services and Find My sync..."
sleep 120
```

## Step 5: Full health check with auto-recovery

### 5a. Services & processes on iMac

```bash
ssh evelyn@192.168.50.6 "echo '=== UPTIME ==='; uptime; echo; echo '=== LAUNCHAGENTS ==='; launchctl list | grep airtrackr; echo; echo '=== PROCESSES ==='; pgrep -fl orchestrated_tracker; pgrep -fl uvicorn; pgrep -fl black_screen; pgrep -fl FindMy; pgrep -fl caffeinate"
```

Verify ALL of the following are running:
- `orchestrated_tracker.py` (tracker)
- `uvicorn swift_api:app` (API, LaunchAgent com.airtrackr.api)
- `black_screen` (LaunchAgent com.airtrackr.blackscreen)
- `FindMy` (Find My app)
- `caffeinate` (prevent sleep)
- com.airtrackr.dim ran successfully (exit code 0)

### 5b. Auto-recover failed services

If any service is not running, attempt to restart it:

```bash
ssh evelyn@192.168.50.6 '
# Check and restart black_screen if not running
if ! pgrep -f black_screen > /dev/null; then
    echo "Restarting black_screen..."
    launchctl kickstart -k gui/$(id -u)/com.airtrackr.blackscreen
    sleep 2
fi

# Check and restart tracker if not running
if ! pgrep -f orchestrated_tracker > /dev/null; then
    echo "Restarting tracker..."
    cd ~/Repos/airtrackr
    source venv/bin/activate
    nohup python orchestrated_tracker.py >> logs/tracker.log 2>&1 &
    sleep 2
fi

# Check and restart caffeinate if not running
if ! pgrep -f caffeinate > /dev/null; then
    echo "Starting caffeinate..."
    nohup caffeinate -dims > /dev/null 2>&1 &
fi

# Verify
echo "=== SERVICE STATUS AFTER RECOVERY ==="
pgrep -fl orchestrated_tracker && echo "Tracker: OK" || echo "Tracker: FAIL"
pgrep -fl uvicorn && echo "API: OK" || echo "API: FAIL"
pgrep -fl black_screen && echo "Black screen: OK" || echo "Black screen: FAIL"
pgrep -fl caffeinate && echo "Caffeinate: OK" || echo "Caffeinate: FAIL"
'
```

### 5c. Re-enable VNC legacy mode (password-only auth)

After reboot, ensure VNC uses simple password auth instead of macOS login:

```bash
ssh evelyn@192.168.50.6 '
echo "evelyn" | sudo -S /System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart \
    -configure \
    -clientopts -setvnclegacy -vnclegacy yes \
    -setvncpw -vncpw airtrackr 2>&1 | tail -2
'
```

### 5d. SSH tunnel on MacBook

```bash
launchctl list | grep airtrackr
```

Verify tunnel is running:
- `com.airtrackr.tunnel-combined` (API port 8001 + VNC port 5901)

If tunnel shows exit code 255, restart it:

```bash
# Restart tunnel if needed
launchctl kickstart -k gui/$(id -u)/com.airtrackr.tunnel-combined 2>/dev/null
sleep 3
launchctl list | grep airtrackr
```

### 5e. VNC tunnel connectivity

```bash
nc -z localhost 5901 && echo "VNC tunnel: OK" || echo "VNC tunnel: FAIL"
```

Do NOT open VNC automatically. If the user needs to check the screen, they can use `/imac-vnc`.

### 5f. API health (via tunnel)

```bash
curl -sL -H "X-API-Key: $(grep AIRTRACKR_API_KEY ~/.secrets | cut -d= -f2)" http://localhost:8001/health/
```

Verify: `status: healthy`, `database_connected: true`.

### 5g. Check extraction status

The tracker needs ~3 minutes to complete a full cycle (People + Devices + Items). Find My may need extra time to sync after reboot.

Check tracker log for extraction progress:

```bash
ssh evelyn@192.168.50.6 "grep -E '(extracted|Saved|Cycle complete)' ~/Repos/airtrackr/logs/tracker.log | tail -15"
```

If extractions show 0 items, wait another 60 seconds and check again:

```bash
echo "Find My may still be syncing, waiting 60s..."
sleep 60
ssh evelyn@192.168.50.6 "grep -E '(extracted|Saved|Cycle complete)' ~/Repos/airtrackr/logs/tracker.log | tail -10"
```

Verify: At least one "Saved X/Y" line with X > 0 appears after the reboot time.

## Step 6: Report results

Present a summary table:

| # | Check | Resultaat |
|---|-------|-----------|
| 1 | Auto-login | ✅/❌ (uptime shows user logged in) |
| 2 | API health | ✅/❌ (healthy, db connected) |
| 3 | Permissions | ✅/❌ (permissions_ok in health) |
| 4 | Tracker | ✅/❌ (PID) |
| 5 | Find My | ✅/❌ (PID) |
| 6 | Caffeinate | ✅/❌ |
| 7 | Black screen | ✅/❌ |
| 8 | Dim screen | ✅/❌ (exit 0) |
| 9 | SSH tunnel (MacBook) | ✅/❌ (tunnel-combined) |
| 10 | VNC tunnel | ✅/❌ (port 5901) |
| 11 | Extractie | ✅ X/3 tabs |

If any check FAILS after auto-recovery, highlight it clearly and suggest manual intervention.

## Troubleshooting

### VNC asks for username+password instead of just password
Run the VNC legacy mode command from step 5c again.

### Tracker extracts 0 items
Find My needs time to sync with iCloud. Wait 2-3 minutes after reboot and check again.

### Black screen not running
The binary might be compiled for wrong architecture. Check with:
```bash
ssh evelyn@192.168.50.6 "file ~/Repos/airtrackr/swift/black_screen"
```
Should show `x86_64` for Intel iMac. Recompile if needed:
```bash
ssh evelyn@192.168.50.6 "cd ~/Repos/airtrackr/swift && swiftc -O -o black_screen black_screen.swift -target x86_64-apple-macos10.15"
```
