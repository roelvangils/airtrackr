---
name: imac-vnc
description: Open a VNC connection to the AirTrackr iMac to visually inspect the screen
disable-model-invocation: true
---

# Open VNC to iMac

Opens a VNC connection to the AirTrackr iMac (192.168.50.6) via the SSH tunnel on port 5901.

## Step 1: Check SSH tunnel

```bash
launchctl list | grep airtrackr
```

If the tunnel shows exit code 255 or is not running, restart it:

```bash
launchctl kickstart -k gui/$(id -u)/com.airtrackr.tunnel-combined 2>/dev/null
sleep 3
launchctl list | grep airtrackr
```

## Step 2: Verify VNC tunnel connectivity

```bash
nc -z localhost 5901 && echo "VNC tunnel: OK" || echo "VNC tunnel: FAIL"
```

If FAIL, the tunnel may not be configured correctly. Check `~/Library/LaunchAgents/com.airtrackr.tunnel-combined.plist`.

## Step 3: Ensure VNC legacy mode is enabled

VNC should use simple password auth (not macOS login):

```bash
ssh evelyn@192.168.50.6 '
echo "evelyn" | sudo -S /System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart \
    -configure \
    -clientopts -setvnclegacy -vnclegacy yes \
    -setvncpw -vncpw airtrackr 2>&1 | tail -2
'
```

## Step 4: Open VNC

```bash
open "vnc://localhost:5901"
```

Tell the user: "VNC is open — wachtwoord is `airtrackr`."

## What to check

When connected via VNC, verify:

1. **Black screen overlay** - The screen should be completely black (black_screen app running)
2. **Find My app** - Should be visible underneath if you move/close black_screen
3. **Terminal** - Running start_all_imac.sh with tracker status
4. **Console** - Showing tracker.log

## Troubleshooting

### VNC asks for username+password instead of just password
Run step 3 again to re-enable VNC legacy mode.

### Connection refused
The SSH tunnel may be down. Run:
```bash
launchctl kickstart -k gui/$(id -u)/com.airtrackr.tunnel-combined
```

### Black screen not visible
Check if black_screen is running:
```bash
ssh evelyn@192.168.50.6 "pgrep -fl black_screen"
```

If not running, restart it:
```bash
ssh evelyn@192.168.50.6 "launchctl kickstart -k gui/\$(id -u)/com.airtrackr.blackscreen"
```
