# AirTrackr iMac Setup Guide

How to set up AirTrackr on a remote Intel iMac, accessible via SSH through a jump server.

## Prerequisites

- macOS with Find My app
- Python 3.13 (via Homebrew)
- SSH access (optionally via jump server)
- VNC access for GUI operations
- Find My must be logged into the same Apple ID as your AirTags/devices

## Network Architecture

```
MacBook (local)
    |
    | SSH / VNC
    v
Jump Server (kumulus.11ways.be)
    |
    | SSH (192.168.50.6)
    v
Intel iMac (evelyn@192.168.50.6)
    - Find My app (GUI)
    - Swift extractor (Accessibility APIs)
    - SQLite database
    - FastAPI REST API (port 8001)
```

## Initial Setup

### 1. SSH Access

```bash
# Direct (two hops)
ssh roel@kumulus.11ways.be
ssh evelyn@192.168.50.6

# Or single command with ProxyJump
ssh -J roel@kumulus.11ways.be evelyn@192.168.50.6
```

### 2. VNC Access (for GUI operations)

```bash
# SSH tunnel for VNC
ssh -L 5901:localhost:5900 -J roel@kumulus.11ways.be evelyn@192.168.50.6 -N
```

Then connect with: `vnc://localhost:5901` (Finder > Go > Connect to Server)

### 3. Install Dependencies

```bash
# Clone or rsync the repo
cd ~/Repos
# Option A: git clone (if HTTPS works)
git clone https://github.com/<user>/airtrackr.git

# Option B: rsync from MacBook (if HTTPS is blocked)
# Run from MacBook:
rsync -avz --exclude 'venv/' --exclude 'database/' --exclude '.git/' \
  --exclude 'node_modules/' --exclude 'dashboard/dist/' \
  -e "ssh -J roel@kumulus.11ways.be" \
  /path/to/airtrackr/ evelyn@192.168.50.6:~/Repos/airtrackr/

# Create venv and install
cd ~/Repos/airtrackr
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**If HTTPS is blocked** (pip can't download packages), install offline:

```bash
# On MacBook: download wheels
pip download -r requirements.txt -d /tmp/wheels --platform macosx_10_9_x86_64 \
  --python-version 313 --only-binary=:all:

# Copy to iMac
scp -o ProxyJump=roel@kumulus.11ways.be /tmp/wheels/*.whl evelyn@192.168.50.6:/tmp/wheels/

# On iMac: install from local wheels
pip install --no-index --find-links /tmp/wheels -r requirements.txt
```

### 4. Compile Swift Binary

```bash
# On iMac (Intel only)
cd ~/Repos/airtrackr/swift
swiftc -O -o airtag_extractor airtag_extractor.swift \
  -framework ApplicationServices -framework Foundation

# Or universal binary (if both architectures needed)
./build_universal.sh
```

### 5. Initialize Database

```bash
cd ~/Repos/airtrackr
source venv/bin/activate
python3 -c "from db import init_schema; init_schema()"
```

### 6. Copy Database from MacBook (optional)

To sync existing location history from another machine:

```bash
# On MacBook: export and copy
scp -o ProxyJump=roel@kumulus.11ways.be \
  database/airtracker.db evelyn@192.168.50.6:~/Repos/airtrackr/database/

# Or selectively import via SQL dump
sqlite3 database/airtracker.db ".dump swift_locations" > /tmp/locations.sql
scp -o ProxyJump=roel@kumulus.11ways.be /tmp/locations.sql evelyn@192.168.50.6:/tmp/
ssh -J roel@kumulus.11ways.be evelyn@192.168.50.6 \
  'sqlite3 ~/Repos/airtrackr/database/airtracker.db < /tmp/locations.sql'
```

### 7. Copy Geocoding Cache

Important if HTTPS is blocked on the iMac (can't reach Nominatim):

```bash
# On MacBook: export geocoding cache
sqlite3 database/airtracker.db "SELECT * FROM geocoding_cache WHERE latitude IS NOT NULL" \
  > /tmp/geocache.sql

# Copy to iMac and import
```

### 8. Seed Location Aliases

Check that Home/Work aliases are configured:

```bash
sqlite3 ~/Repos/airtrackr/database/airtracker.db \
  "SELECT alias, real_address FROM location_aliases"
```

Expected:
```
Home|Onderstraat 7, 9000 Ghent
Work|Kouter 7, 9000 Ghent
Ouderlijk huis|Kleiryt 42, 2330 Merksplas
```

### 9. Run Backfill (if database was copied)

```bash
cd ~/Repos/airtrackr && source venv/bin/activate
python3 backfill_enrichment.py
```

This enriches historical records with:
- Location timestamps
- Distance from home
- Trip detection
- Visit/dwell time detection

## Find My Setup

Find My must be open and showing devices. From VNC:

1. Open Find My app
2. Verify you're logged into iCloud (System Settings > Apple ID)
3. Ensure devices/items/people are visible and up to date
4. **Do not minimize Find My** -- the tracker reads via Accessibility APIs

### Accessibility Permissions

The terminal app (Ghostty/Terminal) needs Accessibility permissions:

System Settings > Privacy & Security > Accessibility > Enable Ghostty

**Note:** Accessibility permissions are per-app. If you run the tracker from SSH, `sshd` would need permissions (not recommended). Always run from a GUI terminal.

## Starting AirTrackr

### Quick Start (paste in Ghostty on iMac)

```bash
~/Repos/airtrackr/start_tracker_imac.sh
```

This single command:
1. Opens Console.app with the error log
2. Arranges windows (Find My left, Ghostty top-right, Console bottom-right)
3. Enables caffeinate (prevents sleep, screen can turn off)
4. Starts the orchestrated tracker

### Start API Server Separately

```bash
cd ~/Repos/airtrackr && source venv/bin/activate
python3 -m uvicorn swift_api:app --host 192.168.50.6 --port 8001
```

### Auto-Start After Reboot (LaunchAgent)

A LaunchAgent is installed at `~/Library/LaunchAgents/com.airtrackr.plist`.

Load it **from Ghostty** (not SSH):

```bash
launchctl load ~/Library/LaunchAgents/com.airtrackr.plist
```

This auto-starts both the API and tracker at login with caffeinate.

To unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.airtrackr.plist
```

## Accessing the API from MacBook

The API runs on `192.168.50.6:8001`. Due to a network issue on the iMac (local TCP connections fail, likely Tailscale-related), standard SSH port forwarding doesn't work. Use one of these methods:

### Method 1: Query via Jump Server

```bash
ssh roel@kumulus.11ways.be "curl -s http://192.168.50.6:8001/api/v1/devices" | python3 -m json.tool
```

### Method 2: TCP Proxy on Jump Server

```bash
ssh -L 8001:localhost:8001 roel@kumulus.11ways.be -t \
  'python3 -c "
import socket,threading
def fwd(s,d):
 try:
  while 1:
   x=s.recv(4096)
   if not x:break
   d.sendall(x)
 except:pass
 finally:s.close();d.close()
srv=socket.socket()
srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
srv.bind((\"127.0.0.1\",8001));srv.listen(5)
print(\"Proxy ready: localhost:8001 -> 192.168.50.6:8001\")
while 1:
 c,a=srv.accept();r=socket.socket();r.connect((\"192.168.50.6\",8001))
 threading.Thread(target=fwd,args=(c,r),daemon=True).start()
 threading.Thread(target=fwd,args=(r,c),daemon=True).start()
"'
```

Then access: `http://localhost:8001/api/v1/devices`

### Key API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/devices` | All devices (paginated) |
| `GET /api/v1/devices/{name}` | Device details |
| `GET /api/v1/devices/{name}/history` | Location history |
| `GET /api/v1/devices/{name}/trips` | Detected trips |
| `GET /api/v1/devices/{name}/visits` | Visit/dwell times |
| `GET /api/v1/locations/latest` | Latest location per device |
| `GET /api/v1/devices/{name}/export?format=csv` | Export data |

## Monitoring

### Error Log

Located at `~/Desktop/airtrackr_errors.log`. Open in Console.app:

```bash
open -a Console ~/Desktop/airtrackr_errors.log
```

### Tracker Log

```bash
tail -f ~/Repos/airtrackr/logs/tracker.log
```

### API Log

```bash
tail -f ~/Repos/airtrackr/logs/api.log
```

### Database Stats

```bash
sqlite3 ~/Repos/airtrackr/database/airtracker.db "
  SELECT 'Locations' as type, COUNT(*) as count FROM swift_locations
  UNION ALL
  SELECT 'Devices', COUNT(*) FROM swift_devices
  UNION ALL
  SELECT 'Trips', COUNT(*) FROM trips
  UNION ALL
  SELECT 'Visits', COUNT(*) FROM visits;
"
```

## Troubleshooting

### "No location found" for all devices
Find My needs to sync. Open Find My via VNC, wait for locations to update.

### Tracker can't switch tabs (AppleScript timeout)
The tracker must run from a GUI terminal (Ghostty), not SSH. Ensure Ghostty has Accessibility permissions.

### Geocoding fails (HTTPS blocked)
The iMac may not have outbound HTTPS. Copy the geocoding cache from a machine that does:
```bash
# From MacBook
sqlite3 database/airtracker.db ".dump geocoding_cache" > /tmp/geocache.sql
scp -o ProxyJump=roel@kumulus.11ways.be /tmp/geocache.sql evelyn@192.168.50.6:/tmp/
ssh -J roel@kumulus.11ways.be evelyn@192.168.50.6 \
  'sqlite3 ~/Repos/airtrackr/database/airtracker.db < /tmp/geocache.sql'
```

### Database "is locked"
Usually transient with WAL mode. If persistent, check for zombie Python processes:
```bash
ps aux | grep python | grep -v grep
```

### caffeinate not working (Mac goes to sleep)
Verify caffeinate is running: `pgrep caffeinate`. Also check System Settings > Energy Saver > "Prevent automatic sleeping when the display is off".
