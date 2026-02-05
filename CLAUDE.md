# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AirTrackr is a macOS application that tracks Apple AirTag, device, and people locations over time. It uses a Swift binary that reads the Find My app via macOS Accessibility APIs and stores location history in an SQLite database. A FastAPI REST API and Vue.js dashboard provide access to the data.

## Technology Stack

- **Python 3.13** (via pyenv, with venv)
- **Swift** — compiled universal binary for macOS Accessibility API extraction
- **SQLite** — data storage (airtracker.db + geocoding_cache.db)
- **FastAPI/Uvicorn** — REST API server (port 8001)
- **Vue.js/Vite** — web dashboard (port 3000, uses Bun)
- **Nominatim (OpenStreetMap)** — geocoding with local cache
- **AppleScript** — Find My tab automation

## Project Structure

```
airtrackr/
├── orchestrated_tracker.py   # Main tracker: cycles People/Devices/Items tabs
├── swift_tracker.py          # Simpler tracker (single tab, no automation)
├── swift_api.py              # FastAPI REST API server
├── findmy_automation.py      # AppleScript tab switching (Cmd+1/2/3)
├── geocoding.py              # Nominatim geocoding with caching
├── health_check.py           # Database health monitoring
├── database_maintenance.py   # Schema cleanup & optimization
├── config.json               # App configuration
│
├── swift/
│   ├── airtag_extractor       # Compiled universal binary (Intel + ARM)
│   ├── airtag_extractor.swift # Source code
│   └── build_universal.sh     # Build script
│
├── dashboard/                 # Vue.js/Vite frontend
│   ├── src/                   # Source files
│   ├── dist/                  # Production build
│   └── package.json           # Bun dependencies
│
├── database/
│   ├── airtracker.db          # Main database
│   └── geocoding_cache.db     # Geocoding lookup cache
│
├── logs/                      # Runtime logs (tracker, api, dashboard)
├── venv/                      # Python virtual environment
├── start_all.sh               # Launch everything
├── start_servers.sh           # API + Dashboard only
├── start_tracker.sh           # Tracker only
└── stop_servers.sh            # Stop all services
```

## Common Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start everything (API + Dashboard + Tracker)
./start_all.sh

# Or start components individually
source venv/bin/activate
uvicorn swift_api:app --host 0.0.0.0 --port 8001      # API
cd dashboard && bun run dev                              # Dashboard
python orchestrated_tracker.py                           # Tracker

# Stop all services
./stop_servers.sh

# Database access
sqlite3 database/airtracker.db
```

## Architecture

```
Find My app (macOS)
    │
    ├── AppleScript tab automation (Cmd+1/2/3)
    │
    ▼
Swift airtag_extractor (Accessibility APIs → JSON)
    │
    ▼
orchestrated_tracker.py (Python orchestration)
    │
    ├── Geocoding (Nominatim + cache)
    │
    ▼
SQLite Database
    │
    ▼
FastAPI REST API (port 8001)
    │
    ▼
Vue.js Dashboard (port 3000)
```

## Database Schema

Two main tables:
- **swift_locations** — all location records (device_name, location, time_status, distance, lat/lon, device_type, timestamp)
- **swift_devices** — device summary (device_name, first_seen, last_seen, last_location, update_count, device_type)

Indexes on: device_name, timestamp DESC, extracted_at, device_type.

## API Endpoints

Base URL: `http://localhost:8001`

- `GET /health` — health check
- `GET /devices` — list all devices
- `GET /devices/{name}` — device details
- `GET /devices/{name}/history` — location history
- `GET /locations/latest` — latest location per device
- `GET /locations/search` — search locations
- `GET /stats/{device_name}` — device statistics
- `GET /docs` — Swagger UI

## Key Notes

- The Swift extractor requires Accessibility permissions in System Preferences
- Find My app must be open for the tracker to work
- Geocoding uses a 1.1s rate limit for Nominatim (free tier)
- The orchestrated tracker cycles through all 3 tabs in ~3 minutes
- Dashboard uses Google Maps iframe for location display
