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
- Accessibility permissions granted to Terminal/IDE
- Find My app open with devices visible

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
- **Tracker** — collects location data every ~3 minutes
- **API** — REST server at http://localhost:8001
- **Dashboard** — web UI at http://localhost:3000

### 4. View Your Data

- **Dashboard**: http://localhost:3000
- **API docs**: http://localhost:8001/docs
- **Stop all**: `./stop_servers.sh`

### 5. Backfill Historical Data (optional)

If you have existing data from before the enrichment features, run:

```bash
source venv/bin/activate
python backfill_enrichment.py --dry-run   # Preview what will change
python backfill_enrichment.py             # Run all enrichment steps
```

## Architecture

```
Find My app (macOS)
    |
    +-- AppleScript tab automation (Cmd+1/2/3)
    |
    v
Swift airtag_extractor (Accessibility APIs -> JSON)
    |
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

The Swift binary reads device names, locations, distances, and timestamps directly from the Find My UI. No screenshots or OCR needed.

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

Create `~/Library/LaunchAgents/com.airtrackr.plist` to auto-start on login. See `setup.sh` for details.

## Project Structure

```
airtrackr/
├── orchestrated_tracker.py   # Main tracker with tab cycling + enrichment
├── swift_tracker.py          # Simple single-tab tracker
├── swift_api.py              # FastAPI REST API
├── db.py                     # Shared database module (schema, migrations, sanitization)
├── enrichment.py             # Distance from home, trip detection, visit tracking
├── geocoding.py              # Nominatim geocoding + structured addresses + reverse geocoding
├── retention.py              # Data aggregation (raw -> hourly -> daily)
├── backfill_enrichment.py    # Retroactive data enrichment for historical records
├── findmy_automation.py      # AppleScript tab automation
├── config.json               # Configuration
├── swift/                    # Swift extractor (source + compiled binary)
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
