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
├── orchestrated_tracker.py   # Main tracker: cycles People/Devices/Items
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
    │  Accessibility APIs — the Swift binary drives the View menu,
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

- switches tabs itself (`--tab people|devices|items`) via Find My's **View menu**, and
  **verifies** the switch landed before reading, so rows can never be filed under the
  wrong `device_type`
- stands down while the Mac is in use (`--require-idle`), because reading Find My means
  stealing focus
- exits with a specific code per failure — never exits 0 on failure:

  | code | meaning |
  |------|---------|
  | 0 | rows extracted |
  | 2 | Accessibility permission missing |
  | 3 | Find My not running (pass `--launch`) |
  | 4 | running but no window |
  | 5 | tab switch failed |
  | 6 | tab verified but empty |
  | 7 | unexpected AX error |
  | 8 | someone is using the Mac; nothing was touched |
  | 64 | bad arguments |

```bash
swift/airtag_extractor --tab items --launch --pretty
swift/airtag_extractor --tab people --include-raw   # show each row's raw AX text
swift/airtag_extractor --print-idle                 # seconds since last user input
swift/airtag_extractor --tab items --require-idle 300
```

**Street addresses come from selecting rows** (`--details`, on by default via
`automation.read_details`). The plain list shows only a coarse label ("Ghent"); the
*selected* row's accessibility text carries the street ("Kortrijksesteenweg, Ghent").
The sweep selects each row in turn (~0.3s per row) and the tracker prefers that
address for the `location` column and for geocoding. Three traps, all learned the
hard way:

- **Never press an already-selected row** — that second press acts as a double-click
  and opens the detail view, which replaces the whole list (CardContainerView
  disappears; every read then exits 4). The sweep harvests selected rows without
  pressing, and a View-menu tab press restores the list if a detail view opens anyway.
- **The list re-sorts itself live** as freshness labels change, so row indexes are
  meaningless across a press. The sweep tracks rows by name, never position.
- **Rows below the fold don't enrich when selected off-screen** — `AXScrollToVisible`
  first.

Note the precision trade-off: a street without a house number geocodes to the street's
centroid, which for a long street can be further from the truth than the city label was.
The *label* is always better; the coordinates usually are.

**The `Me` tab is currently unreachable.** Find My's View menu has items for People,
Devices and Items but none for Me, and pressing the Me tab button does not navigate
(see below), so there is no way in. `orchestrated_tracker.py` cycles three tabs, not
four. `--tab me` still exists and will start working on its own if Apple adds the menu
item back.

### When Find My changes again

Dump the live accessibility tree first — never guess at the structure:

```bash
swift swift/ax_dump.swift        # runs directly, no build step
```

Current structure (Find My 5.0, macOS 27) and the assumptions that depend on it:

- Find My is a **Mac Catalyst** app. Its tree is UIKit-shaped, and **element depth
  is not stable between reads** — navigate structurally, never by index or depth.
- `AXGroup id="CardContainerView"` is the only anchor relied on. Scoping to it is
  what excludes the map subtree (pins are `AXGenericElement` siblings), so no
  string blacklist is needed.
- A row is any element whose children carry an identifier made of one or more
  `ListEntityRow` joined by `-` — see the next section.
- Fields are classified by shape, not position. The location line joins
  place/time/battery with `·` (U+00B7).
- Tabs are `AXRadioButton` + `AXSubrole AXTabButton`, with `AXValue` 1/0 for selected.
  **`AXValue` is a lie about which tab is showing** — see below.
- `AXHeading` under `AXGroup id="PrimaryLabel"` names the active tab, and is the only
  thing trusted to confirm a switch.

### Three behaviours that are easy to get wrong

1. **Pressing a tab button does not change tab.** `AXUIElementPerformAction(…, kAXPressAction)`
   on a tab bar `AXRadioButton` returns `.success`, flips that button's `AXValue` to 1,
   and navigates nowhere: the heading and the rows both keep showing the previous tab.
   Tab changes go through **View > People/Devices/Items** instead, which lands in under
   a second. This matters beyond convenience — trusting the button's `AXValue` would
   file Items rows as Devices. Verify with the heading, never the button.
2. **A row's labels arrive merged into one element.** What used to be three
   `AXStaticText` children each identified `ListEntityRow` is now a single element whose
   text is those labels joined with `", "` and whose identifier is their identifiers
   joined with `-`, e.g. `ListEntityRow-ListEntityRow-ListEntityRow` for
   `"Roel's Keys, 2,2 km, Home · 13 min. ago"`. The repeat count is the field count, so
   splitting off exactly `count - 1` leading segments recovers the fields without
   shredding a location line that itself contains `", "` (`"Langemunt, Ghent"`). Note a
   bare comma is a decimal separator here (`2,2 km`) — split on `", "` only.
3. **Find My only builds its window when activated.** Launched in the background it
   sits there with zero accessible windows. `--launch` reopens it to recover.

Consequence: the tracker steals focus for a moment on each cycle. That is inherent
to reading Find My this way, and is why the pause below exists.

## Pausing while the Mac is in use

Since a cycle yanks Find My to the front, the tracker refuses to run while someone is
working. Any input pauses it immediately; it resumes once the Mac has been idle for
`automation.resume_after_idle_seconds` in `config.json` (default 300, `0` disables).
The extractor enforces this itself (`--require-idle`, exit 8) so the check cannot be
skipped by a caller, and the tracker checks separately before it does anything
disruptive of its own, like launching Find My.

A pause is **not** a failure: it must never feed the consecutive-failure counter or
trigger a Find My restart, or a long working session would look like a broken app.

Two traps here:

- **Do not measure idle time with `kCGAnyInputEventType`.** On macOS 27 it reports
  activity every ~5 seconds on a Mac nobody is touching, while every individual input
  type simultaneously reports minutes of quiet. `userIdleSeconds()` enumerates real
  input types instead.
- **Never fake input.** `simulate_mouse_jiggle()` exists to do exactly that and is
  therefore unused: synthetic input is indistinguishable from the real thing, so it
  would make the tracker pause itself after every keepalive.

Both `.hidSystemState` and `.combinedSessionState` are consulted, most recent wins:
work over Screen Sharing shows up only in the latter, and physical typing only in the
former.

## Database

Main tables: `swift_locations` (every reading) and `swift_devices` (per-device summary),
plus `geocoding_cache`, `trips`, `visits`, `zones`, `location_summaries`.

Schema changes go in `db.py` as a numbered `_migrate_to_vN`, with `SCHEMA_VERSION`
bumped; `init_schema()` applies whatever is pending via `PRAGMA user_version`.

**Every timestamp in the database is UTC** (schema v6), and the API attaches an
explicit `+00:00`/`Z` on the way out so consumers convert for display. This is a rule
with a history: before v6, `extracted_at` and `location_timestamp` were local while
`timestamp` and `last_seen` were UTC, and every comparison that crossed that line was
silently wrong — the duplicate check never suppressed a row, and retention cutoffs and
period filters were shifted by the local offset (compounded by `isoformat()`'s "T"
sorting after the stored format's space). Never compare a timestamp against
`datetime.now()`; it is `datetime.now(timezone.utc)` with a `%Y-%m-%d %H:%M:%S` format,
everywhere.

The data was restarted fresh on 2026-08-18 (pre-v6 file in `database/backups/`,
gitignored). The WAL is bounded by `journal_size_limit`; `database/` must never be
committed — a rollback journal once sat in the public git history carrying real
location data, and the whole path is purged and ignored (`*.db-wal`/`-shm` included).

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

The env var wins over the file, which is a trap worth knowing: a key exported in a
shell is invisible to a LaunchAgent, so the agent falls back to `.api_key` — and if that
is absent, it serves everything unauthenticated. Keep the three in sync
(`~/.secrets`, `.api_key`, `dashboard/.env`). Consumers outside this repo use the same
key; `kortex` requires `AIRTRACKR_API_KEY` and fails loudly without it.

The dashboard derives the API URL from the browser's hostname, so opening it from
another machine points it at *that* machine's port 8001. The API binds `127.0.0.1`, so
view it over an SSH tunnel (`-L 3000:localhost:3000 -L 8001:localhost:8001`) rather
than by exposing the port.

Vite inlines that key into the built bundle, which is why `dashboard/dist/` is
gitignored. Do not commit it.

## Running under launchd

`./launchd/install.sh` installs four agents: `display` (virtual display + Find My,
see prepare-session.sh), `api`, `tracker`, and `dashboard`. All are LaunchAgents with
`LimitLoadToSessionType Aqua`, and that is not a detail: Accessibility only works inside
a GUI login session, so the tracker cannot run at the login window. It starts when the
user logs in, not at boot — keep auto-login on if it must survive a reboot unattended.

The dashboard agent serves the **built** bundle (`vite preview` over `dashboard/dist`),
not the dev server. Two consequences: `install.sh` rebuilds the bundle on every install
because Vite inlines `VITE_API_KEY` at build time (a rotated key needs a rebuild, or the
dashboard keeps sending the old one), and `bun run dev` on port 3000 will conflict with
the agent — stop it first (`launchctl bootout gui/$(id -u)/com.airtrackr.dashboard`)
when developing.

Two permission walls, and they fail in different ways:

- **Accessibility.** Granted per executable, and a grant to Terminal does not cover a
  LaunchAgent — the responsible process there is the agent's program
  (`venv/bin/python`). A launchd process is refused *in silence*: nothing prompts and
  nothing appears in System Settings to switch on. The tracker therefore calls
  `airtag_extractor --request-permission` when it sees exit 2, which registers it in
  the Accessibility list so it can be toggled.
- **Automation (Apple Events).** A LaunchAgent has no Automation grant and cannot be
  prompted for one, and `osascript` calls to System Events then *hang* until their
  timeout rather than failing. This turned every cycle into a minute of "Failed to check
  if FindMy is running". Anything on the critical path must avoid Apple Events:
  `is_find_my_running()` uses `pgrep`, launching uses `open -b com.apple.findmy`, and
  window recovery is left to the extractor. The remaining AppleScript in
  `findmy_automation.py` is recovery-path only and will be slow under launchd.

## Notes

- Geocoding is rate-limited to 1.1s per request (Nominatim free tier).
- A full cycle over the three tabs takes ~25s, and runs on a 1-minute schedule
  (~90s effective cadence, since the next run is scheduled after the previous one
  finishes). The table does not grow with the cadence: `is_duplicate` writes a row
  only when a device's location CHANGED, plus one heartbeat row per device per hour.
  Freshness is a separate signal — every cycle touches `swift_devices.last_seen`
  even when it writes nothing, and the API's `minutes_since_update` derives from it.
  That is how "parked for two days" (old location row, fresh last_seen) stays
  distinguishable from "scrape broken for two days" (both old).
- Find My glues a "Live" indicator onto the address of people sharing their live
  position; the extractor strips it, because it flips between reads and would
  otherwise register the same house as a move each time.
- Logs are bounded for unattended operation: tracker.log rotates itself (10MB x 3),
  and prepare-session.sh trims any other logs/*.log past 20MB. launchd appends by
  file descriptor, so trimming truncates in place — moving the file would leave the
  writer appending to the old inode forever.
- Readings Find My labels as hours/days old are rejected outright (`_STALE_TIME_RE` in
  `db.py`): they are a stale memory, not a location update.
