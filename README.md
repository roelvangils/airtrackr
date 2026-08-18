# AirTrackr

AirTrackr tracks Apple AirTag, device, and people locations over time by reading the Find My app via macOS Accessibility APIs. It provides what Apple doesn't: **a complete location history** with a REST API and web dashboard.

## Why AirTrackr?

Apple's Find My app doesn't provide:
- Historical location data
- API access to locations
- Export functionality
- Trip or visit history

AirTrackr solves this by reading Find My directly through macOS Accessibility APIs, storing every location update in a database, and exposing it through a REST API and web dashboard.

## Features

- **Direct Find My extraction** — Swift binary reads the Find My UI via Accessibility APIs (no screenshots or OCR)
- **Automated tab cycling** — Cycles through People, Devices, and Items tabs every ~3 minutes
- **Geocoding** — Converts addresses to GPS coordinates via OpenStreetMap Nominatim, with structured address components (street, city, postal code, country)
- **Data enrichment** — Distance from home, trip detection, visit/dwell time tracking
- **REST API** — FastAPI server with device listing, history, search, trips, visits, statistics, and export
- **Web dashboard** — Vanilla JS frontend with Leaflet maps, pagination, date filtering, and export
- **Data retention** — Automatic aggregation of old data into hourly/daily summaries
- **Backfill** — Retroactive enrichment of historical data

## Prerequisites

- macOS (Ventura or later recommended)
- Python 3.13+
- Bun (for dashboard development)
- Accessibility permissions granted to whatever runs the extractor
- Find My app running **with a window**

### The display requirement (read this before installing on a headless Mac)

AirTrackr reads the Find My app through the accessibility API, so Find My must have a
window. A window needs a framebuffer, and a Mac running headless has none: a MacBook with
the lid shut and no external display reports **zero displays** in IORegistry, and then *no*
application can open a window at all — not Find My, and not a virtual-display app either.

If this is missing there is no error. The extractor simply returns nothing, and anything
downstream falls back to coarse positions — several distinct items reported at one
identical coordinate, which looks like real data and is not.

Any one of these satisfies it:

| Option | Notes |
|---|---|
| Keep the lid open | Free, but easy to forget |
| A dummy HDMI/USB-C plug | ~€10, survives reboots, nothing to configure |
| **DeskPad** (virtual display) | Installed by `launchd/install.sh`; needs Screen Recording approval once |

With DeskPad, also install `displayplacer`: DeskPad starts at 3360x2100, which makes the
machine unusable over screen sharing. `launchd/install.sh` installs both, and the tracker
resets the resolution to 1920x1080 on each run.

Verify the requirement is met:

```bash
# Should be >= 1. Zero means no app can open a window.
ioreg -c AppleDisplay -r | grep -c AppleDisplay

# Should be 1.
osascript -e 'tell application "System Events" to tell process "FindMy" to return count of windows'
```

### Permissions to grant once

| Grant to | Where | Why |
|---|---|---|
| The extractor's parent process | Privacy & Security > **Accessibility** | Reading the Find My UI |
| DeskPad | Privacy & Security > **Screen Recording** | Creating the virtual display |
| Your shell or `bun`/`python` | Privacy & Security > **Full Disk Access** | Only if reading protected caches |

Each shows a dialog on first use. Until it is approved the corresponding step fails
silently rather than loudly — if extraction returns nothing, check these first.

## Quick Start

### 1. Setup

```bash
git clone https://github.com/yourusername/airtrackr.git
cd airtrackr

# Run setup (creates venv, installs deps, builds Swift binary)
./setup.sh

# Or manually:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Grant Accessibility Permissions

Go to **System Settings > Privacy & Security > Accessibility** and add your terminal app (Terminal, iTerm2, etc.).

### 3. Start Everything

```bash
./start_all.sh
```

This launches:
- **Tracker** — cycles People / Devices / Items / Me, about 90 seconds per pass
- **API** — REST server at http://localhost:8001
- **Dashboard** — web UI at http://localhost:3000

### 4. View Your Data

- **Dashboard**: http://localhost:3000
- **API docs**: http://localhost:8001/docs
- **Stop all**: `./stop_servers.sh`

### 5. Check a Single Pass (optional)

To see exactly what the tracker reads without writing anything to the database:

```bash
venv/bin/python orchestrated_tracker.py --dry-run
```

## Architecture

```
Find My app (macOS)
    |
    v
Swift airtag_extractor (Accessibility APIs -> JSON)
    |  switches tabs itself and verifies the switch landed
    |  before reading, so rows can't be filed under the wrong type
    v
orchestrated_tracker.py (Python orchestration)
    |
    +-- Geocoding (Nominatim + cache)
    +-- Enrichment (distance from home, trips, visits)
    |
    v
SQLite Database
    |
    v
FastAPI REST API (:8001)
    |
    v
Vanilla JS Dashboard (:3000)
```

The Swift binary reads device names, locations, distances, battery and timestamps directly from the Find My UI. No screenshots or OCR needed.

Find My must be frontmost for a tab switch to register, so the extractor briefly brings it to the front on each cycle. See CLAUDE.md for the accessibility-tree details and for how to diagnose the next time Apple changes Find My.

## API Endpoints

All endpoints are under `/api/v1/`. Full interactive docs at http://localhost:8001/docs (Swagger UI).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Health check with DB stats |
| GET | `/api/v1/devices` | List all tracked devices (paginated) |
| GET | `/api/v1/devices/counts` | Count by type (people/devices/items) |
| GET | `/api/v1/devices/{name}` | Device details |
| GET | `/api/v1/devices/{name}/history` | Location history with date filtering |
| GET | `/api/v1/devices/{name}/export` | Export as CSV, JSON, or GPX |
| GET | `/api/v1/devices/{name}/trips` | Detected trips (paginated) |
| GET | `/api/v1/devices/{name}/visits` | Visit/dwell times (paginated) |
| GET | `/api/v1/devices/{name}/zone` | Check if device is in a geofence |
| GET | `/api/v1/locations/latest` | Latest location per device |
| GET | `/api/v1/locations/search` | Search locations by text/device/date |
| GET | `/api/v1/stats/{name}` | Device statistics |
| GET/POST/DELETE | `/api/v1/zones` | Manage geofencing zones |
| POST | `/api/v1/track` | Trigger a tracking cycle |

Pagination uses `?limit=50&offset=0`. Responses include `total` and `has_more`.

## Database

SQLite database at `database/airtracker.db`. Schema version is managed via `PRAGMA user_version` (currently v3).

### Tables

| Table | Purpose |
|-------|---------|
| `swift_locations` | Location history (device, location, coords, timestamp, distance from home, battery status) |
| `swift_devices` | Device summary (name, type, first/last seen, update count) |
| `geocoding_cache` | Nominatim results with structured address fields (street, city, postal code, country) |
| `trips` | Detected movement between locations (start/end coords, distance, duration) |
| `visits` | Dwell time at locations (arrival, departure, duration) |
| `location_summaries` | Aggregated hourly/daily summaries (from retention) |
| `zones` | Geofencing zones |
| `location_aliases` | Maps Find My display names to real addresses (e.g. "Home" -> real address) |

```bash
# Quick queries
sqlite3 database/airtracker.db "SELECT device_name, location, timestamp FROM swift_locations ORDER BY timestamp DESC LIMIT 10;"

# Export to CSV
sqlite3 -header -csv database/airtracker.db \
  "SELECT device_name, location, latitude, longitude, distance_from_home_km, timestamp FROM swift_locations ORDER BY timestamp DESC;" > locations.csv
```

## Configuration

Edit `config.json` to customize:

- **geocoding.rate_limit_seconds** — Nominatim rate limit (default: 1.1s)
- **geocoding.cache_duration_days** — How long to cache geocoding results (default: 7 days)
- **database.retention.raw_data_days** — Keep raw records for N days before aggregating (default: 90)
- **database.retention.hourly_summary_days** — Keep hourly summaries for N days (default: 365)

### Location Aliases

Map Find My display names to real addresses for geocoding:

```sql
sqlite3 database/airtracker.db "INSERT INTO location_aliases (alias, address) VALUES ('Home', 'Your Address, City');"
```

## Running 24/7

**Option 1 — Start scripts (recommended):**
```bash
./start_all.sh    # Starts tracker, API, and dashboard in background
./stop_servers.sh  # Stops everything
```

**Option 2 — macOS LaunchAgent:**

```bash
./launchd/install.sh              # install + start the API and tracker agents
./launchd/install.sh --uninstall  # stop and remove them
```

The tracker needs Accessibility permission, and macOS grants it per executable — a grant to Terminal does not cover a LaunchAgent. macOS prompts on the first cycle; until it's granted, the extractor exits 2 and says so in `logs/tracker.log`.

## Project Structure

```
airtrackr/
├── orchestrated_tracker.py   # Main tracker with tab cycling + enrichment
├── swift_api.py              # FastAPI REST API
├── db.py                     # Shared database module (schema, migrations, validation)
├── enrichment.py             # Distance from home, trip detection, visit tracking
├── geocoding.py              # Nominatim geocoding + structured addresses + reverse geocoding
├── retention.py              # Data aggregation (raw -> hourly -> daily)
├── findmy_automation.py      # Find My process lifecycle
├── config.json               # Configuration
├── swift/                    # Swift extractor (source + compiled binary + ax_dump)
├── launchd/                  # LaunchAgent templates + install.sh
├── dashboard/                # Vanilla JS / Vite frontend
├── database/                 # SQLite database
├── logs/                     # Runtime logs
├── start_all.sh              # Launch everything
├── start_servers.sh          # API + Dashboard only
├── start_tracker.sh          # Tracker only
└── stop_servers.sh           # Stop all services
```

## Troubleshooting

**Tracker not extracting data:**
- Is Find My open and visible?
- Are Accessibility permissions granted?
- Check `logs/tracker.log` for errors

**API not responding:**
- Check `logs/api.log`
- Verify port 8001 is free: `lsof -i :8001`

**Geocoding failures:**
- Nominatim has a 1 request/second limit
- Check the `geocoding_cache` table for cached results
- See `logs/tracker.log` for geocoding errors

## License

This project is for personal use. Use responsibly and in accordance with local laws and Apple's terms of service.
