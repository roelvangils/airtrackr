# Debugging AirTrackr

Almost every failure is in extraction — Find My changing shape underneath us. Work
outward from the Swift binary; if that's healthy, the Python layers usually are too.

## 1. Is extraction working?

```bash
swift/airtag_extractor --tab items --launch --pretty
echo "exit: $?"
```

The exit code is the diagnosis:

| code | meaning | what to do |
|------|---------|------------|
| 0 | rows extracted | extraction is fine, move to step 3 |
| 2 | Accessibility permission missing | grant it to *this* process (see below) |
| 3 | Find My not running | pass `--launch` |
| 4 | running, no window | `--launch` reopens it |
| 5 | tab switch failed | something is stealing focus; see step 2 |
| 6 | tab verified but empty | normal for `me`; otherwise go to step 2 |
| 7 | unexpected AX error | the tree changed mid-read; retry, then step 2 |

Failures also print a JSON object with an `error` block explaining the cause, so
`2>/dev/null` still tells you what went wrong.

### Accessibility permission

macOS grants this **per executable**. Granting it to Terminal does not cover
`venv/bin/python` running under a LaunchAgent — that needs its own grant, under
System Settings > Privacy & Security > Accessibility.

## 2. Did Find My change?

Dump the live tree rather than guessing:

```bash
swift swift/ax_dump.swift                       # whole tree
swift swift/ax_dump.swift | grep ListEntityRow  # just the device rows
swift swift/ax_dump.swift --depth 6             # structure only
```

Compare against the structure documented in `CLAUDE.md`. What matters:

- `AXGroup id="CardContainerView"` still exists (everything is scoped to it)
- rows still expose children with `AXIdentifier == "ListEntityRow"`
- tabs are still `AXSubrole == "AXTabButton"` with `AXValue` 1/0
- the location line still joins its parts with `·` (U+00B7)

**Element depth is not stable between reads** — if you are counting levels to find
something, that's the bug.

To see the strings a row actually produced, before any parsing:

```bash
swift/airtag_extractor --tab items --include-raw --pretty
```

Rows the parser can't classify appear in the top-level `warnings` array rather than
being dropped silently.

## 3. Is the tracker storing what it reads?

```bash
venv/bin/python orchestrated_tracker.py --dry-run
```

Reads one full cycle and prints what it *would* write, touching nothing. If
extraction is fine but rows aren't landing, the difference is in
`db.sanitize_device_data`, which deliberately skips:

- rows with no location (`has_location: false`)
- stale rows — hours, days, weeks or months old
- a bare `Paused` with no location attached

`logs/tracker.log` records each skip at DEBUG level.

## 4. Logs

```bash
tail -f logs/tracker.log     # tracker, INFO and above
tail -f logs/api.log         # API access + errors
```

The tracker logs the tab it *verified* it read, which is what `device_type` is
stored from. A line like

```
Requested Items but extractor verified 'Devices'; storing rows as 'device'
```

means Find My handed us a different tab than asked for — logged, and stored
correctly, rather than silently mislabelled.

## 5. API and database

```bash
curl -H "X-API-Key: $AIRTRACKR_API_KEY" localhost:8001/api/v1/health
sqlite3 database/airtracker.db "SELECT device_type, COUNT(*) FROM swift_locations GROUP BY device_type"
```

Two things worth knowing:

- If neither `AIRTRACKR_API_KEY` nor `.api_key` exists, **auth is silently
  disabled** — an unexpected 401 means the key is set but the client isn't sending
  a matching one.
- `DB_PATH` is anchored to the repo and overridable with `AIRTRACKR_DB`. If the API
  reports zero devices, confirm it opened the database you think it did — `/health`
  returns `database_path`.

## Known non-bugs

- **Duplicate device names.** Find My genuinely shows two rows called
  "Roel's Backpack" and two called "Left Bud". Both are stored in
  `swift_locations`; `swift_devices` collapses them because `device_name` is
  `UNIQUE`. The accessibility tree offers no stable per-row id to key on.
- **Missing distance.** While a row refreshes, Find My replaces its distance label
  with a spinner. `distance` is null on those reads and reappears on the next one.
- **The `me` tab is empty.** Exit 6 there is expected.
- **Find My comes to the front each cycle.** A tab press is silently ignored unless
  the app is frontmost, so this is unavoidable when reading it this way.
