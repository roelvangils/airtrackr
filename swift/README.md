# Swift Find My Extractor

Reads People / Devices / Items / Me rows out of the macOS Find My app through the
Accessibility APIs, and prints them as JSON. No screenshots, no OCR.

## Components

1. **airtag_extractor.swift** — the extractor. Switches tabs, verifies the switch,
   reads the rows.
2. **ax_dump.swift** — prints Find My's raw accessibility tree. This is what you
   run first when Apple changes Find My and extraction starts coming back empty.

## Building

```bash
./build_universal.sh      # universal binary (x86_64 + arm64) -> ./airtag_extractor
```

## Usage

```bash
./airtag_extractor --tab items --launch --pretty

./airtag_extractor --tab people          # people | devices | items | me
./airtag_extractor                        # read whichever tab is already open
./airtag_extractor --tab items --include-raw   # show each row's raw AX strings
./airtag_extractor --help
```

JSON goes to stdout, progress and errors to stderr.

### Exit codes

Failure is never reported as exit 0 — the previous version printed `[]` and exited
successfully for every problem including a revoked Accessibility grant, which is how
a broken tracker went unnoticed for months.

| code | kind | meaning |
|------|------|---------|
| 0 | ok | rows extracted |
| 2 | ax_not_trusted | Accessibility permission missing |
| 3 | app_not_running | Find My not running (pass `--launch`) |
| 4 | no_window | running, but no window to read |
| 5 | tab_switch_failed | the tab did not become active |
| 6 | no_rows | tab verified but empty — normal for Me |
| 7 | ax_error | unexpected accessibility error |
| 64 | usage | bad arguments |

Every failure still prints a JSON object with `"ok": false` and an `error` block, so
a caller that only captured stdout can still report why.

## Requirements

- macOS 26 or later, with Find My 5.0+ (the Catalyst rewrite)
- Accessibility permission for whichever process runs this binary — macOS grants it
  per executable, so a grant to Terminal does not cover a LaunchAgent

Find My does **not** need to be on any particular tab, and does not need to be open:
`--launch` will start it. It does get brought to the front during a tab switch —
see below.

## How it works

1. Finds the Find My process by bundle id (`com.apple.findmy`).
2. Locates `AXGroup id="CardContainerView"` and scopes everything to it, which
   structurally excludes the map's pins and points of interest.
3. Presses the requested `AXTabButton`, then polls until both the button's `AXValue`
   and the `AXHeading` under `PrimaryLabel` confirm the switch.
4. Waits for the row count to stop changing.
5. Collects every element whose children carry `AXIdentifier == "ListEntityRow"` and
   classifies each row's text by shape rather than by position.

### Two things that will bite you

- **A tab switch requires Find My to be frontmost.** `AXUIElementPerformAction` with
  `kAXPressAction` on a backgrounded Find My returns `.success` and silently does
  nothing. The extractor activates the app first; `--no-activate` turns that off, and
  then switching will not work.
- **Find My only creates its window when activated.** Launched in the background it
  runs with zero accessible windows.

## Output

```json
{
  "ok": true,
  "schema_version": 2,
  "tab": "Items",
  "tab_verified": true,
  "extracted_at": "2026-08-03T16:31:14Z",
  "count": 2,
  "warnings": [],
  "devices": [
    {
      "index": 0,
      "name": "Auto",
      "has_location": true,
      "location": "Ghent",
      "location_parts": ["Ghent", "8 min. ago", "Quarter-Charged Battery"],
      "address_unavailable": false,
      "time_status": "8 min. ago",
      "proximity": null,
      "distance": { "value": 1.3, "unit": "km", "text": "1.3 km", "km": 1.3 },
      "battery": "Quarter-Charged",
      "favorite": false
    },
    { "index": 1, "name": "Dongle bag", "has_location": false, "location_parts": [] }
  ]
}
```

Keys whose value is absent are omitted rather than set to `null`, so read them
defensively. Distance is normalised to a dot decimal regardless of locale — Find My
renders it in the user's format (`"1,3 km"` in Belgium).

`--format legacy` emits the old flat array (`name`/`location`/`timeStatus`/`distance`)
for anything still expecting the pre-2026 shape.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| exit 2 | Grant Accessibility to the process that runs the binary, not just Terminal |
| exit 4 | Find My is windowless; `--launch` reopens it |
| exit 5 | Something else is stealing focus during the switch |
| exit 6 on a tab you expect rows in | `swift ax_dump.swift` — the tree may have changed |
| rows parsed into the wrong fields | `--include-raw` to see the strings Find My actually gave us |
