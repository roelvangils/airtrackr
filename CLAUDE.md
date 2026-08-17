# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AirTrackr tracks Apple AirTag, device, and people locations over time. A Swift binary reads the Find My app through the macOS Accessibility APIs and stores location history in SQLite. A FastAPI REST API and a vanilla-JS dashboard expose the data.

Everything runs locally on this Mac. (Through mid-2026 it ran on a remote iMac; that deployment is retired and its scripts have been removed.)

## Technology Stack

- **Python 3.13+** (3.14 locally, via venv)
- **Swift** — universal binary (Intel + Apple Silicon) for Accessibility extraction
- **SQLite** — `database/airtracker.db`
- **FastAPI/Uvicorn** — REST API on port 8001
- **Vanilla JS + Vite + Leaflet** — dashboard on port 3000 (built with Bun)
- **Nominatim (OpenStreetMap)** — geocoding, with a local cache table

## Project Structure

```
airtrackr/
├── orchestrated_tracker.py   # Main tracker: cycles People/Devices/Items/Me
├── swift_api.py              # FastAPI REST API server
├── db.py                     # Schema, migrations, shared connection
├── findmy_automation.py      # Find My process lifecycle (no longer switches tabs)
├── geocoding.py              # Nominatim geocoding with caching
├── enrichment.py             # Distance from home, trips, visits
├── retention.py              # Aggregates old rows into summaries
├── database_maintenance.py   # Schema cleanup & optimization
├── config.json               # App configuration (gitignored)
│
├── swift/
│   ├── airtag_extractor       # Compiled universal binary
│   ├── airtag_extractor.swift # Source
│   ├── ax_dump.swift          # Dumps Find My's accessibility tree (see below)
│   └── build_universal.sh     # Build script
│
├── dashboard/                 # Vite frontend (dist/ is gitignored)
├── launchd/                   # LaunchAgent templates + install.sh
├── database/                  # SQLite (gitignored)
└── logs/                      # Runtime logs (gitignored)
```

## Common Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
swift/build_universal.sh

# Run everything
./start_all.sh          # API + dashboard + tracker
./stop_servers.sh

# Or individually
venv/bin/uvicorn swift_api:app --host 127.0.0.1 --port 8001
cd dashboard && bun run dev
venv/bin/python orchestrated_tracker.py --schedule 5

# Inspect one pass without writing anything
venv/bin/python orchestrated_tracker.py --dry-run

# Run as background services
./launchd/install.sh            # install + start
./launchd/install.sh --uninstall

sqlite3 database/airtracker.db
```

## Architecture

```
Find My app (macOS)
    │  Accessibility APIs — the Swift binary presses the tab button,
    │  confirms the switch landed, then reads the rows
    ▼
swift/airtag_extractor  ──JSON──▶  orchestrated_tracker.py
                                        │
                                        ├── geocoding (Nominatim + cache)
                                        ├── enrichment (trips, visits)
                                        ▼
                                   SQLite ──▶ FastAPI :8001 ──▶ dashboard :3000
```

## The Swift extractor

`swift/airtag_extractor` is the piece that breaks when Apple changes Find My. It:

- switches tabs itself (`--tab people|devices|items|me`) and **verifies** the switch
  landed before reading, so rows can never be filed under the wrong `device_type`
- exits with a specific code per failure — never exits 0 on failure:

  | code | meaning |
  |------|---------|
  | 0 | rows extracted |
  | 2 | Accessibility permission missing |
  | 3 | Find My not running (pass `--launch`) |
  | 4 | running but no window |
  | 5 | tab switch failed |
  | 6 | tab verified but empty (normal for Me) |
  | 7 | unexpected AX error |
  | 64 | bad arguments |

```bash
swift/airtag_extractor --tab items --launch --pretty
swift/airtag_extractor --tab people --include-raw   # show each row's raw AX text
```

### When Find My changes again

Dump the live accessibility tree first — never guess at the structure:

```bash
swift swift/ax_dump.swift        # runs directly, no build step
```

Current structure (Find My 5.0, macOS 26/27) and the assumptions that depend on it:

- Find My is a **Mac Catalyst** app. Its tree is UIKit-shaped, and **element depth
  is not stable between reads** — navigate structurally, never by index or depth.
- `AXGroup id="CardContainerView"` is the only anchor relied on. Scoping to it is
  what excludes the map subtree (pins are `AXGenericElement` siblings), so no
  string blacklist is needed.
- A row is any element whose children carry `AXIdentifier == "ListEntityRow"`.
- Row fields live in separate `AXStaticText` children; the location line joins
  place/time/battery with `·` (U+00B7). Fields are classified by shape, not position.
- Tabs are `AXRadioButton` + `AXSubrole AXTabButton`, with `AXValue` 1/0 for selected.
- `AXHeading` under `AXGroup id="PrimaryLabel"` names the active tab.

### Two behaviours that are easy to get wrong

1. **Find My must be frontmost for a tab switch.** `AXUIElementPerformAction(…, kAXPressAction)`
   on a backgrounded Find My returns `.success` and does nothing at all. The
   extractor activates the app before pressing. `--no-activate` disables that, and
   then tab switching will not work.
2. **Find My only builds its window when activated.** Launched in the background it
   sits there with zero accessible windows. `--launch` reopens it to recover.

Consequence: the tracker steals focus for a moment on each cycle. That is inherent
to reading Find My this way.

## Database

Main tables: `swift_locations` (every reading) and `swift_devices` (per-device summary),
plus `geocoding_cache`, `trips`, `visits`, `zones`, `location_summaries`.

Schema changes go in `db.py` as a numbered `_migrate_to_vN`, with `SCHEMA_VERSION`
bumped; `init_schema()` applies whatever is pending via `PRAGMA user_version`.

`DB_PATH` is anchored to the repo (override with `AIRTRACKR_DB`). It used to be
relative, which silently created a second empty database when the process was
started from another directory.

Known limitation: `swift_devices.device_name` is `UNIQUE`, but Find My legitimately
shows duplicate names (two "Roel's Backpack", two "Left Bud"). Both rows are stored
in `swift_locations`; they collapse to one in `swift_devices`. The accessibility
tree exposes no stable per-row identifier to key on.

## API

Base URL `http://localhost:8001`, routes under `/api/v1` (unprefixed paths redirect).
`GET /docs` for Swagger.

Auth is via the `X-API-Key` header, read from `AIRTRACKR_API_KEY` or a `.api_key`
file. **If neither exists, authentication is silently disabled.** The dashboard
reads the same key from `dashboard/.env` as `VITE_API_KEY`.

Vite inlines that key into the built bundle, which is why `dashboard/dist/` is
gitignored. Do not commit it.

## Notes

- The Swift extractor needs Accessibility permission, granted **per executable** —
  a grant to Terminal does not cover a LaunchAgent.
- Geocoding is rate-limited to 1.1s per request (Nominatim free tier).
- A full cycle over the four tabs takes ~90 seconds.
