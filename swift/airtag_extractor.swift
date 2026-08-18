// airtag_extractor.swift
//
// Extracts People / Devices / Items / Me rows from the macOS Find My app via the
// Accessibility APIs, and emits structured JSON on stdout.
//
// Targets Find My 5.0 on macOS 27, which is a Mac Catalyst app. Its accessibility
// tree looks like this (depth is NOT stable between reads — navigate structurally):
//
//   AXWindow id="SceneWindow"
//   └ AXGroup sub=iOSContentGroup
//     └ AXGroup id="FindMy.Application"
//       └ AXGroup id="CardContainerView"        <- the only anchor we rely on
//         ├ AXGroup
//         │ ├ AXGroup id="PrimaryLabel"
//         │ │ └ AXHeading desc="Items"          <- name of the active tab, and the
//         │ │                                      ONLY trusted proof of a switch
//         │ └ … └ AXGroup                       <- one per row
//         │       └ AXStaticText id="ListEntityRow-ListEntityRow-ListEntityRow"
//         │              desc="Roel's Keys, 2,2 km, Home · 13 min. ago · Quarter-Charged Battery"
//         └ AXTabGroup
//           └ 4x AXRadioButton sub=AXTabButton desc="People"|"Devices"|"Items"|"Me"
//                                                     ^ state only; pressing these
//                                                       does NOT navigate
//
// macOS 27 changed two things that each broke this tool outright, both handled below:
//
//   1. A row's labels are merged into ONE element whose text is the old labels joined
//      with ", " and whose identifier is the old identifiers joined with "-". Rows used
//      to be several AXStaticText children each identified plain "ListEntityRow".
//      See listEntityRowFieldCount / splitMergedRowText.
//   2. Pressing a tab bar AXRadioButton no longer navigates — it only flips the
//      button's own AXValue. Tab changes go through the View menu instead.
//      See switchToTab.
//
// Scoping the walk to CardContainerView is what keeps the map subtree out: pins are
// AXGenericElement siblings ("Auto,Map pin", "My Location") and POIs are
// AXGenericElement sub=AXMapItem id="VKPointFeature". No string blacklist needed.

import Foundation
import ApplicationServices
import AppKit

// MARK: - Exit codes

enum ExitCode: Int32 {
    case ok = 0
    case axNotTrusted = 2
    case appNotRunning = 3
    case noWindow = 4
    case tabSwitchFailed = 5
    case noRows = 6
    case axError = 7
    case userActive = 8
    case usage = 64

    var kind: String {
        switch self {
        case .ok: return "ok"
        case .axNotTrusted: return "ax_not_trusted"
        case .appNotRunning: return "app_not_running"
        case .noWindow: return "no_window"
        case .tabSwitchFailed: return "tab_switch_failed"
        case .noRows: return "no_rows"
        case .axError: return "ax_error"
        case .userActive: return "user_active"
        case .usage: return "usage"
        }
    }
}

let schemaVersion = 2
let findMyBundleID = "com.apple.findmy"

func warn(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

// MARK: - User activity

/// The event types that mean a person is actually doing something. Deliberately
/// enumerated rather than using kCGAnyInputEventType (0xFFFFFFFF): on macOS 27 that
/// wildcard reports activity every ~5 seconds on an untouched Mac, while every
/// individual input type below simultaneously reports minutes of quiet. Something
/// periodic and not user-driven is counted by the wildcard, so gating on it means
/// never running at all.
///
/// Up events and mouse-moved-with-button are omitted as redundant; flagsChanged is
/// included so holding a modifier counts as presence.
let userInputEventTypes: [CGEventType] = [
    .mouseMoved, .leftMouseDown, .rightMouseDown, .otherMouseDown,
    .leftMouseDragged, .rightMouseDragged, .otherMouseDragged,
    .keyDown, .flagsChanged, .scrollWheel, .tabletPointer,
]

/// Seconds since the person using this Mac last did anything.
///
/// Reading Find My means stealing focus, so the tracker must stand down while the Mac
/// is in use. This is the measurement that decides that.
///
/// Both event sources are consulted and the most recent wins, because neither alone
/// covers how this Mac is actually used:
///
///   - `.hidSystemState` sees only physical hardware input. Someone working over
///     Screen Sharing never touches it — measured at 2979s on a machine that was
///     being actively driven remotely.
///   - `.combinedSessionState` also counts posted events, which is where Screen
///     Sharing input lands, and it tracked that same session to within a second.
///
/// Taking the minimum is safe for this tool specifically because it posts no synthetic
/// events of its own: tabs are changed with AXPress on a menu item, which is not a
/// CGEvent and therefore never registers as user activity. If that ever changes — a
/// synthetic click, say — this function would start reporting the tool's own input as
/// the user's and the pause would never engage. The same trap applies to anything
/// else in this project that fakes input; see simulate_mouse_jiggle.
func userIdleSeconds() -> Double {
    var idle = Double.greatestFiniteMagnitude
    for type in userInputEventTypes {
        idle = min(idle,
                   CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: type),
                   CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: type))
    }
    return idle
}

// MARK: - Accessibility helpers

/// Every AX read funnels through here so `invalidUIElement` (-25202, the tree was
/// rebuilt under us) stays distinguishable from "attribute simply absent".
var sawStaleElement = false

func copyAttr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    switch err {
    case .success:
        return value
    case .invalidUIElement:
        sawStaleElement = true
        return nil
    default:
        return nil
    }
}

func axString(_ element: AXUIElement, _ name: String) -> String? {
    copyAttr(element, name) as? String
}

func axInt(_ element: AXUIElement, _ name: String) -> Int? {
    (copyAttr(element, name) as? NSNumber)?.intValue
}

func axChildren(_ element: AXUIElement) -> [AXUIElement] {
    copyAttr(element, kAXChildrenAttribute as String) as? [AXUIElement] ?? []
}

func axRole(_ element: AXUIElement) -> String? { axString(element, kAXRoleAttribute as String) }
func axSubrole(_ element: AXUIElement) -> String? { axString(element, kAXSubroleAttribute as String) }
func axIdentifier(_ element: AXUIElement) -> String? { axString(element, kAXIdentifierAttribute as String) }
func axDescription(_ element: AXUIElement) -> String? { axString(element, kAXDescriptionAttribute as String) }

/// Description, falling back to value — Catalyst puts row text in AXDescription,
/// but not every build is consistent about it.
func axText(_ element: AXUIElement) -> String? {
    let raw = axDescription(element) ?? axString(element, kAXValueAttribute as String)
    guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
        return nil
    }
    return trimmed
}

let maxDepth = 40

func findDescendant(_ element: AXUIElement, depth: Int = 0, where predicate: (AXUIElement) -> Bool) -> AXUIElement? {
    if depth > maxDepth { return nil }
    if predicate(element) { return element }
    for child in axChildren(element) {
        if let hit = findDescendant(child, depth: depth + 1, where: predicate) { return hit }
    }
    return nil
}

// MARK: - Text classification

func regex(_ pattern: String) -> NSRegularExpression {
    // Patterns are compile-time constants; a throw here is a programming error.
    return try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
}

// Not a raw string: the \u{...} escapes must be resolved by Swift into real NBSP and
// narrow-NBSP characters. ICU would not understand Swift's brace form if passed through.
let distanceRE = regex("^([\\d.,\u{00A0}\u{202F} ]+?)\\s*(m|km|mi|ft|yd)$")
let timeAgoRE = regex(#"^\d+\s*(min|mins|minute|minutes|hr|hrs|hour|hours|day|days|week|weeks|mo|month|months|yr|year|years)\.?\s+ago$"#)
let timeWordRE = regex(#"^(now|just now|yesterday|paused)$"#)

func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
    re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
}

/// U+00B7 MIDDLE DOT joins the segments of the location line.
let separator: Character = "\u{00B7}"

/// ", " joins the *fields* of a row — see splitMergedRowText. Deliberately the
/// two-character sequence, not a bare comma: a comma with no space after it is a
/// decimal separator in this locale ("2,2 km") and must never be treated as a join.
let fieldJoiner = ", "

let noLocationLiterals: Set<String> = ["no location found", "location not available", "no location"]
let proximityLiterals: Set<String> = ["nearby", "here", "with you"]
let addressUnavailableLiterals: Set<String> = ["address unavailable", "no address found"]

/// Presence indicators Find My glues onto a person's address ("Kleiryt, Merksplas,
/// Live") that come and go between reads. State, not geography — they must never
/// survive into an address or the same house reads as a move on every flip.
let presenceLiterals: Set<String> = ["live"]

/// Labels that describe an action rather than the device, contributed by rows that
/// expose themselves as a button (package-tracking rows do).
let actionLabels: Set<String> = ["open details", "show details"]

func isNoLocation(_ s: String) -> Bool { noLocationLiterals.contains(s.lowercased()) }
func isProximity(_ s: String) -> Bool { proximityLiterals.contains(s.lowercased()) }
func hasSeparator(_ s: String) -> Bool { s.contains(separator) }
func isBattery(_ s: String) -> Bool { s.lowercased().contains("battery") }
func isTime(_ s: String) -> Bool { matches(timeWordRE, s) || matches(timeAgoRE, s) }

// MARK: - Distance

struct Distance: Codable {
    let value: Double
    let unit: String
    let text: String   // normalized, dot decimal — feeds the legacy `distance` TEXT column
    let km: Double
}

let kmPerUnit: [String: Double] = ["m": 0.001, "km": 1.0, "mi": 1.609344, "ft": 0.0003048, "yd": 0.0009144]

/// Parses the numeric part of a distance without NumberFormatter, which would make
/// the result depend on the machine's locale. Find My renders in the user's locale
/// ("1,3 km" here in Belgium, "1.3 km" in the US), so both are accepted.
func parseNumber(_ raw: String) -> Double? {
    var s = raw
    for space in [" ", "\u{00A0}", "\u{202F}"] { s = s.replacingOccurrences(of: space, with: "") }
    guard !s.isEmpty else { return nil }

    let lastDot = s.lastIndex(of: ".")
    let lastComma = s.lastIndex(of: ",")

    // Whichever of . or , comes last is the decimal separator; the rest group digits.
    var decimalIndex: String.Index?
    switch (lastDot, lastComma) {
    case let (dot?, comma?):
        decimalIndex = dot > comma ? dot : comma
    case let (dot?, nil):
        decimalIndex = dot
    case let (nil, comma?):
        decimalIndex = comma
    case (nil, nil):
        decimalIndex = nil
    }

    // A separator followed by exactly 3 digits with no further separator is a
    // thousands group ("1.234 km"), not a decimal point.
    if let idx = decimalIndex {
        let fractionDigits = s.distance(from: s.index(after: idx), to: s.endIndex)
        if fractionDigits == 3 { decimalIndex = nil }
    }

    var normalized = ""
    for (offset, ch) in zip(s.indices, s) {
        if ch == "." || ch == "," {
            if offset == decimalIndex { normalized.append(".") }
            // otherwise: thousands separator, drop it
        } else {
            normalized.append(ch)
        }
    }
    return Double(normalized)
}

func parseDistance(_ text: String) -> Distance? {
    let range = NSRange(text.startIndex..., in: text)
    guard let m = distanceRE.firstMatch(in: text, range: range),
          let numberRange = Range(m.range(at: 1), in: text),
          let unitRange = Range(m.range(at: 2), in: text) else { return nil }

    guard let value = parseNumber(String(text[numberRange])) else { return nil }
    let unit = String(text[unitRange]).lowercased()
    guard let factor = kmPerUnit[unit] else { return nil }

    // Trim a trailing ".0" so whole numbers read as "86 km", not "86.0 km".
    let rendered = value == value.rounded() && abs(value) < 1e15
        ? String(Int(value))
        : String(value)

    return Distance(value: value, unit: unit, text: "\(rendered) \(unit)", km: value * factor)
}

func isDistance(_ s: String) -> Bool { parseDistance(s) != nil }

// MARK: - Model

struct DeviceRow: Codable {
    var index: Int
    let name: String
    let hasLocation: Bool
    let location: String?
    let locationParts: [String]
    let addressUnavailable: Bool
    let timeStatus: String?
    let proximity: String?
    let distance: Distance?
    let battery: String?
    let batteryRaw: String?
    let favorite: Bool
    /// Street-level address, harvested by selecting the row (--details). The plain
    /// list shows only a coarse label ("Ghent"); the SELECTED row's accessibility
    /// text carries the street ("Kortrijksesteenweg, Ghent"). nil when the sweep was
    /// off, or this row's enrichment did not land in time.
    var address: String?
    let texts: [String]?   // only with --include-raw
}

struct ExtractionError: Codable {
    let code: Int32
    let kind: String
    let message: String
    let tabRequested: String?
    let tabObserved: String?
}

struct Envelope: Codable {
    let ok: Bool
    let schemaVersion: Int
    let tab: String?
    let tabRequested: String?
    let tabVerified: Bool
    let extractedAt: String
    let appPid: Int32?
    let count: Int
    let warnings: [String]
    let devices: [DeviceRow]
    let error: ExtractionError?
}

/// The pre-macOS-26 output shape, kept behind --format legacy for swift_tracker.py.
struct LegacyDevice: Codable {
    let name: String
    let location: String
    let timeStatus: String
    let distance: String
    let batteryStatus: String?
    let extractedAt: String
}

// MARK: - Row identity

/// How many of a row's labels this one element represents, or nil if it is not part
/// of a row at all.
///
/// Find My 5.0 on macOS 27 merges a row's separate `AXStaticText` labels into a single
/// accessibility element, and builds the merged element's identifier by joining the
/// originals with "-". So what used to arrive as three children each identified
/// "ListEntityRow" now arrives as one child identified
/// "ListEntityRow-ListEntityRow-ListEntityRow". Matching that identifier exactly — as
/// this tool did until macOS 27 — silently finds zero rows on a perfectly healthy tab.
///
/// The repeat count is load-bearing: it tells splitMergedRowText how many fields were
/// merged, which is the only way to know how many of the ", " occurrences in the text
/// are joins rather than part of a field.
func listEntityRowFieldCount(_ element: AXUIElement) -> Int? {
    guard let identifier = axIdentifier(element) else { return nil }
    let parts = identifier.components(separatedBy: "-")
    guard !parts.isEmpty, parts.allSatisfy({ $0 == "ListEntityRow" }) else { return nil }
    return parts.count
}

/// Undoes the label merge described above, recovering the field list the classifier
/// downstream expects.
///
/// Splitting off exactly `fields - 1` leading segments and leaving the remainder whole
/// matters: the last field is the location line, and it is the one that legitimately
/// contains ", " itself ("Langemunt, Ghent · 13 min. ago"). Naively splitting on every
/// ", " would shred it.
func splitMergedRowText(_ text: String, fields: Int) -> [String] {
    guard fields > 1 else { return [text] }
    let parts = text.components(separatedBy: fieldJoiner)
    guard parts.count > 1 else { return [text] }

    let leading: [String] = Array(parts.prefix(fields - 1))
    let remainder: String = parts.dropFirst(fields - 1).joined(separator: fieldJoiner)
    var recovered: [String] = leading
    if !remainder.isEmpty { recovered.append(remainder) }

    return recovered
        .map { (field: String) in field.trimmingCharacters(in: CharacterSet.whitespacesAndNewlines) }
        .filter { (field: String) in !field.isEmpty && !actionLabels.contains(field.lowercased()) }
}

// MARK: - Row parsing

struct ParsedRow {
    let row: DeviceRow
    let warnings: [String]
}

/// Parses a row that is currently SELECTED, whose text is the enriched
/// "Name, Open Details, qualifiers..., address..., time" form. The generic field
/// classifier cannot handle this variant — its fields are comma-joined without the
/// "·" location line, so splitMergedRowText shreds the address and whatever fragment
/// lands last wins the location. That is not hypothetical: a row left selected by an
/// earlier details sweep parsed to location="30 minutes ago" before this existed.
func parseSelectedRow(text: String, index: Int, includeRaw: Bool) -> ParsedRow? {
    var parts = text.components(separatedBy: ", ")
    guard parts.count >= 3, parts[1] == "Open Details" else { return nil }
    let name = parts[0]
    parts.removeFirst(2)

    var timeStatus: String?
    var battery: String?
    var batteryRaw: String?
    if let last = parts.last {
        let segments = last.split(separator: separator).map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let head = segments.first, isTime(head) {
            timeStatus = head
            for segment in segments.dropFirst() where isBattery(segment) {
                batteryRaw = segment
                battery = segment
                    .replacingOccurrences(of: " Battery", with: "", options: .caseInsensitive)
                    .trimmingCharacters(in: .whitespaces)
            }
            parts.removeLast()
        }
    }

    var proximity: String?
    var distance: Distance?
    var zone: String?
    while let first = parts.first {
        if first.lowercased() == "this mac" {
            parts.removeFirst()
        } else if isProximity(first) {
            proximity = first
            parts.removeFirst()
        } else if let d = parseDistance(first) {
            distance = d
            parts.removeFirst()
        } else if first.contains(" | ") {
            // "Home | 2,2 km" — a saved-location label plus the distance to it.
            let halves = first.components(separatedBy: " | ")
            zone = halves[0].trimmingCharacters(in: .whitespaces)
            if halves.count > 1 { distance = parseDistance(halves[1].trimmingCharacters(in: .whitespaces)) }
            parts.removeFirst()
        } else {
            break
        }
    }

    parts.removeAll { presenceLiterals.contains($0.lowercased()) }
    let address: String? = (parts.isEmpty || parts.contains(where: { isNoLocation($0) }))
        ? nil : parts.joined(separator: ", ")
    // `location` is the COARSE label — the zone ("Home") or the city (the address's
    // last component) — never the full street. The plain list shows the coarse label,
    // and the tracker's duplicate check compares raw_data's location field as that
    // stable label; putting the street here defeated the comparison and let sweep
    // misses write spurious move rows. The street lives in `address`, nowhere else.
    let location = zone ?? (address != nil ? parts.last : nil)

    let row = DeviceRow(
        index: index,
        name: name,
        hasLocation: location != nil,
        location: location,
        locationParts: parts,
        addressUnavailable: false,
        timeStatus: timeStatus,
        proximity: proximity,
        distance: distance,
        battery: battery,
        batteryRaw: batteryRaw,
        favorite: false,
        address: address,
        texts: includeRaw ? [text] : nil
    )
    return ParsedRow(row: row, warnings: [])
}

func parseRow(children: [AXUIElement], index: Int, includeRaw: Bool) -> ParsedRow? {
    // A selected row presents as one AXButton with the enriched text; recognise it
    // before the generic splitter gets a chance to shred it.
    for child in children where listEntityRowFieldCount(child) != nil {
        if let raw = axText(child), raw.contains(", Open Details,") {
            return parseSelectedRow(text: raw, index: index, includeRaw: includeRaw)
        }
    }

    var warnings: [String] = []
    var favorite = false
    var texts: [String] = []

    for child in children {
        let role = axRole(child)
        let fields = listEntityRowFieldCount(child)
        if role == "AXImage" {
            // A row that is mid-refresh swaps its distance label for a spinner
            // ("Circular Progress Indicator"); that is normal, not an error.
            if let d = axDescription(child), d.lowercased().contains("favorite") { favorite = true }
        } else if role == "AXStaticText" || (role == "AXButton" && fields != nil) {
            // A row exposed as AXButton rather than AXStaticText is still a row —
            // package-tracking rows present that way. Requiring the row identifier
            // keeps genuine per-row action buttons from contributing stray text.
            if let t = axText(child) {
                texts.append(contentsOf: splitMergedRowText(t, fields: fields ?? 1))
            }
        }
    }

    guard !texts.isEmpty else { return nil }

    // The name is the first text that isn't recognisably one of the other fields.
    // Normally texts[0]; the guard is defensive against a reordered layout.
    guard let nameIndex = texts.firstIndex(where: {
        !isDistance($0) && !isProximity($0) && !isNoLocation($0) && !hasSeparator($0)
    }) else {
        warnings.append("row_\(index)_no_name")
        return nil
    }
    let name = texts[nameIndex]

    var hasLocation = false
    var location: String?
    var locationParts: [String] = []
    var addressUnavailable = false
    var timeStatus: String?
    var proximity: String?
    var distance: Distance?
    var battery: String?
    var batteryRaw: String?

    for (i, text) in texts.enumerated() where i != nameIndex {
        if let d = parseDistance(text) {
            distance = d
        } else if isNoLocation(text) {
            hasLocation = false
        } else if isProximity(text) {
            proximity = text
        } else if hasSeparator(text) {
            // "Ghent · 14 min. ago · Quarter-Charged Battery"
            let segments = text.split(separator: separator).map {
                $0.trimmingCharacters(in: .whitespacesAndNewlines)
            }.filter { !$0.isEmpty }
            guard let place = segments.first else { continue }

            location = place
            locationParts = segments
            hasLocation = true
            if addressUnavailableLiterals.contains(place.lowercased()) { addressUnavailable = true }

            var extras: [String] = []
            for segment in segments.dropFirst() {
                if isBattery(segment) {
                    batteryRaw = segment
                    battery = segment
                        .replacingOccurrences(of: " Battery", with: "", options: .caseInsensitive)
                        .trimmingCharacters(in: .whitespaces)
                } else if isTime(segment) {
                    timeStatus = segment
                } else {
                    extras.append(segment)
                }
            }
            // Forward-compat: an unrecognised trailing segment is almost certainly a
            // time string in a form we haven't seen. Take it, but say so.
            if timeStatus == nil, extras.count == 1, extras[0] == segments.last {
                timeStatus = extras.removeFirst()
                warnings.append("row_\(index)_time_status_unrecognized")
            }
            if !extras.isEmpty {
                location = ([place] + extras).joined(separator: ", ")
            }
        } else if isTime(text) {
            // A row can carry a bare status with no place attached — Find My shows
            // just "Paused" for items whose updates are suspended.
            timeStatus = text
        } else {
            // A bare place name with no separator, e.g. a row showing just "Home".
            location = text
            locationParts = [text]
            hasLocation = true
            warnings.append("row_\(index)_unclassified_single_segment")
        }
    }

    let row = DeviceRow(
        index: index,
        name: name,
        hasLocation: hasLocation && location != nil,
        location: location,
        locationParts: locationParts,
        addressUnavailable: addressUnavailable,
        timeStatus: timeStatus,
        proximity: proximity,
        distance: distance,
        battery: battery,
        batteryRaw: batteryRaw,
        favorite: favorite,
        address: nil,
        texts: includeRaw ? texts : nil
    )
    return ParsedRow(row: row, warnings: warnings)
}

// MARK: - Selected-row enrichment (street addresses)

/// The address hidden in a SELECTED row's accessibility text.
///
/// Find My's list normally shows a coarse place ("Ghent"). Selecting a row — the AX
/// equivalent of clicking it — swaps that row's element for an AXButton whose text
/// carries the full street address, comma-joined:
///
///   "Wallet, Open Details, Nearby, Kortrijksesteenweg, Ghent, 2 minutes ago"
///   "Auto, Open Details, 1,4 km, Guldenspoorstraat, Ghent, 9 minutes ago · Quarter-Charged Battery"
///   "Jelle's Keys, Open Details, Home | 2,2 km, Langemunt, Ghent, 27 minutes ago"
///
/// Structure: name, "Open Details", zero or more qualifiers (proximity, a distance,
/// or "Zone | distance"), then the address parts, then a trailing time (optionally
/// "time · Battery"). Strip the known-typed head and tail; the contiguous middle IS
/// the address. Note the time here uses long units ("minutes ago"), unlike the list's
/// "min. ago" — isTime covers both.
func parseEnrichedAddress(_ text: String) -> String? {
    var parts = text.components(separatedBy: ", ")
    guard parts.count >= 3 else { return nil }
    parts.removeFirst()                                   // the name
    guard parts.first == "Open Details" else { return nil }  // not the enriched variant
    parts.removeFirst()

    // Trailing time, possibly with a battery suffix after the U+00B7 separator.
    if let last = parts.last {
        let head = last.split(separator: separator).first
            .map { $0.trimmingCharacters(in: .whitespaces) } ?? last
        if isTime(head) { parts.removeLast() }
    }

    // Leading qualifiers: "Nearby", "1,4 km", "Home | 2,2 km", "This Mac".
    while let first = parts.first {
        if isProximity(first) || isDistance(first) || first.contains(" | ")
            || first.lowercased() == "this mac" {
            parts.removeFirst()
        } else {
            break
        }
    }

    // A row without a location enriches to things like "No location found" or
    // "RoelPods Pro 2, No location found" (an accessory names its parent product).
    // None of that is an address.
    if parts.contains(where: { isNoLocation($0) }) { return nil }

    parts.removeAll { presenceLiterals.contains($0.lowercased()) }

    let address = parts.joined(separator: ", ").trimmingCharacters(in: .whitespaces)
    return address.isEmpty ? nil : address
}

/// Selects each row in turn and harvests its enriched text.
///
/// Returns enriched row text by row position. Positions are re-resolved from a fresh
/// scan before every press because selection rebuilds the tree; the caller matches the
/// harvest back to its parsed rows by index and verifies the name prefix. Cost is
/// roughly a quarter second per row. The last row is left selected — harmless, since
/// every read that wants addresses runs this sweep anyway.
func sweepRowDetails(pid: pid_t) -> [String] {
    var harvest: [String] = []
    var harvestedNames: [String] = []

    func rowGroups() -> [[AXUIElement]] {
        guard let card = findCardContainer(pid: pid) else { return [] }
        var groups: [[AXUIElement]] = []
        scanRows(card, into: &groups)
        return groups
    }
    func pressable(_ group: [AXUIElement]) -> AXUIElement? {
        group.first { listEntityRowFieldCount($0) != nil }
    }
    func enrichedText(_ group: [AXUIElement]) -> String? {
        guard let el = pressable(group), axRole(el) == "AXButton",
              let text = axText(el), text.contains(", Open Details,") else { return nil }
        return text
    }
    func rowName(_ group: [AXUIElement]) -> String? {
        guard let el = pressable(group), let text = axText(el) else { return nil }
        return text.components(separatedBy: ", ").first
    }

    /// A detail view replaces the whole list (CardContainerView disappears). Pressing
    /// the current tab's View-menu item brings the list back.
    func restoreListIfNeeded() -> Bool {
        if findCardContainer(pid: pid) != nil { return true }
        warn("details: a detail view opened; restoring the list via the View menu")
        let items = viewMenuTabItems(pid: pid)
        guard let anyTab = items.first?.element else { return false }
        let target = items.first(where: { $0.name.lowercased() == currentDetailsTab.lowercased() })?.element ?? anyTab
        AXUIElementPerformAction(target, kAXPressAction as CFString)
        Thread.sleep(forTimeInterval: 1.0)
        return findCardContainer(pid: pid) != nil
    }

    // Work through the rows BY NAME, never by position: Find My re-sorts the list live
    // as freshness labels change, so an index captured before a press may point at a
    // different row after it. That exact shift silently cost most of a sweep's harvest
    // before this was rewritten.
    let names = rowGroups().compactMap(rowName)
    for name in names {
        // Duplicate names (two "Roel's Backpack") are handled by skipping rows already
        // harvested this pass: countOccurrences bookkeeping below.
        let alreadyTaken = harvestedNames.filter { $0 == name }.count

        var attempt = 0
        var found = false
        while attempt < 2 && !found {
            attempt += 1
            let groups = rowGroups()
            // The n-th not-yet-harvested row bearing this name.
            var seen = 0
            var targetGroup: [AXUIElement]?
            for g in groups where rowName(g) == name {
                if seen == alreadyTaken { targetGroup = g; break }
                seen += 1
            }
            guard let group = targetGroup, let element = pressable(group) else { break }

            // NEVER press a row that is already selected: the second press acts like a
            // double-click and opens the detail view, which destroys the entire list.
            if let text = enrichedText(group) {
                harvest.append(text); harvestedNames.append(name); found = true; break
            }
            // Rows below the fold exist in the tree but do not enrich when selected
            // off-screen; scroll them into view first.
            AXUIElementPerformAction(element, "AXScrollToVisible" as CFString)
            Thread.sleep(forTimeInterval: 0.15)
            AXUIElementPerformAction(element, kAXPressAction as CFString)

            let deadline = Date().addingTimeInterval(2.0)
            while Date() < deadline {
                Thread.sleep(forTimeInterval: 0.15)
                let fresh = rowGroups()
                if fresh.isEmpty {
                    // The list vanished — a detail view got opened after all. Restore
                    // and retry this name once.
                    if !restoreListIfNeeded() { return harvest }
                    break
                }
                if let hit = fresh.first(where: {
                    enrichedText($0)?.components(separatedBy: ", ").first == name
                }), let text = enrichedText(hit) {
                    harvest.append(text); harvestedNames.append(name); found = true
                    break
                }
            }
        }
        if !found {
            warn("details: '\(name)' did not enrich within 2s (2 attempts)")
        }
    }
    return harvest
}

/// The tab the details sweep is running against, so its list-restore knows which
/// View-menu item brings the right list back.
var currentDetailsTab = "Items"

// MARK: - Tree scanning

/// Collects row groups by asking "do this element's children carry the ListEntityRow
/// identifier?". Inverting the test this way means we never need AXUIElement to be
/// Hashable, rows come out in document order, and nested groups can't double-count.
func scanRows(_ element: AXUIElement, depth: Int = 0, into rows: inout [[AXUIElement]]) {
    if depth > maxDepth { return }
    let children = axChildren(element)
    if children.contains(where: { listEntityRowFieldCount($0) != nil }) {
        rows.append(children)
        return   // this element IS a row; do not descend further
    }
    for child in children {
        scanRows(child, depth: depth + 1, into: &rows)
    }
}

func findCardContainer(pid: pid_t) -> AXUIElement? {
    let app = AXUIElementCreateApplication(pid)
    guard let windows = copyAttr(app, kAXWindowsAttribute as String) as? [AXUIElement] else { return nil }
    for window in windows {
        if let card = findDescendant(window, where: { axIdentifier($0) == "CardContainerView" }) {
            return card
        }
    }
    return nil
}

/// The Catalyst app is briefly unresponsive to AX right after launch or a tab press.
func findCardContainerWithRetry(pid: pid_t, attempts: Int = 3) -> AXUIElement? {
    for attempt in 0..<attempts {
        if let card = findCardContainer(pid: pid) { return card }
        if attempt < attempts - 1 { Thread.sleep(forTimeInterval: 0.2) }
    }
    return nil
}

func activeTabName(_ card: AXUIElement) -> String? {
    guard let label = findDescendant(card, where: { axIdentifier($0) == "PrimaryLabel" }),
          let heading = findDescendant(label, where: { axRole($0) == "AXHeading" }) else { return nil }
    return axDescription(heading)
}

func tabButtons(_ card: AXUIElement) -> [(name: String, element: AXUIElement, selected: Bool)] {
    var found: [(String, AXUIElement, Bool)] = []
    func walk(_ element: AXUIElement, _ depth: Int) {
        if depth > maxDepth { return }

        if axSubrole(element) == "AXTabButton" || axRole(element) == "AXRadioButton" {
            if axSubrole(element) == "AXTabButton", let name = axDescription(element) {
                found.append((name, element, axInt(element, kAXValueAttribute as String) == 1))
                return
            }
        }
        for child in axChildren(element) { walk(child, depth + 1) }
    }
    walk(card, 0)
    return found
}

func rowCount(pid: pid_t) -> Int {
    guard let card = findCardContainer(pid: pid) else { return -1 }
    var rows: [[AXUIElement]] = []
    scanRows(card, into: &rows)
    return rows.count
}

// MARK: - Tab control

enum TabResult {
    case ok(observed: String?)
    case failed(message: String, observed: String?)
    /// Someone started using the Mac between process start and the focus steal.
    case userActive(idle: Double)
}

/// The menu bar item titled `title`, or nil.
func menuBarMenu(pid: pid_t, title: String) -> AXUIElement? {
    let app = AXUIElementCreateApplication(pid)
    guard let barRef = copyAttr(app, kAXMenuBarAttribute as String),
          CFGetTypeID(barRef) == AXUIElementGetTypeID() else { return nil }
    let bar = barRef as! AXUIElement
    guard let item = axChildren(bar).first(where: {
        axString($0, kAXTitleAttribute as String) == title
    }) else { return nil }
    return axChildren(item).first   // the AXMenu hanging off the AXMenuBarItem
}

/// Find My's View menu opens with one navigation item per list tab ("People",
/// "Devices", "Items"), which is the mechanism this tool uses to change tab.
func viewMenuTabItems(pid: pid_t) -> [(name: String, element: AXUIElement)] {
    guard let menu = menuBarMenu(pid: pid, title: "View") else { return [] }
    // Stop at the first separator: everything past it is map and window commands,
    // not tab navigation.
    var items: [(String, AXUIElement)] = []
    for child in axChildren(menu) {
        guard let title = axString(child, kAXTitleAttribute as String), !title.isEmpty else { break }
        items.append((title, child))
    }
    return items
}

/// Selects a tab and confirms the switch actually landed.
///
/// Navigation goes through the View menu, NOT the tab bar. On Find My 5.0 (macOS 27)
/// `AXUIElementPerformAction(kAXPressAction)` on a tab bar AXRadioButton flips that
/// button's AXValue to 1 and navigates nowhere: the heading and the rows both keep
/// showing the previous tab. That is what used to surface as a 20-second
/// "tab_switch_failed" timeout. Pressing View > <tab> switches in well under a second
/// and leaves the button state consistent too.
///
/// Only the PrimaryLabel heading is accepted as proof of the switch. The tab button's
/// AXValue is explicitly NOT trusted, because the failure above sets it while the list
/// still shows other rows — believing it would file, say, Items rows as Devices.
///
/// Elements are re-resolved from the app on every poll: Catalyst rebuilds the tree on a
/// tab change, so any reference cached across the press goes stale (-25202).
func switchToTab(app: NSRunningApplication, target: String, waitSeconds: Double,
                 activate: Bool, requireIdle: Double = 0) -> TabResult {
    let pid = app.processIdentifier
    guard let card = findCardContainerWithRetry(pid: pid) else {
        return .failed(message: "could not locate CardContainerView", observed: nil)
    }

    // Already there — nothing to do, and no need to steal focus for it.
    if let current = activeTabName(card), current.lowercased() == target.lowercased() {
        return .ok(observed: current)
    }

    let items = viewMenuTabItems(pid: pid)
    guard let item = items.first(where: { $0.name.lowercased() == target.lowercased() })?.element else {
        let available = items.map { $0.name }.joined(separator: ", ")
        return .failed(
            message: "Find My's View menu has no '\(target)' item (available: \(available)). "
                   + "The tab bar button is not a usable fallback: pressing it changes the button's "
                   + "state without navigating.",
            observed: activeTabName(card))
    }

    // Last check before the disruptive part. Launching Find My and recovering its
    // window can take seconds, so the person may have started working since the
    // check at startup — and everything below this line pulls focus away from them.
    if requireIdle > 0 {
        let idle = userIdleSeconds()
        if idle < requireIdle { return .userActive(idle: idle) }
    }

    if activate {
        app.activate()
        Thread.sleep(forTimeInterval: 0.5)
    }

    let err = AXUIElementPerformAction(item, kAXPressAction as CFString)
    guard err == .success else {
        return .failed(message: "AXPress on View > \(target) failed with AXError \(err.rawValue)",
                       observed: activeTabName(card))
    }

    let deadline = Date().addingTimeInterval(waitSeconds)
    var lastObserved: String?
    while Date() < deadline {
        Thread.sleep(forTimeInterval: 0.25)
        guard let card = findCardContainer(pid: pid) else { continue }
        let heading = activeTabName(card)
        lastObserved = heading
        if let heading, heading.lowercased() == target.lowercased() {
            return .ok(observed: heading)
        }
    }

    // Report both signals: disagreement between them is the fingerprint of the
    // AXPress-does-not-navigate bug returning in some new form.
    let buttonState = findCardContainer(pid: pid)
        .flatMap { card in tabButtons(card).first { $0.selected }?.name } ?? "unknown"
    return .failed(
        message: "tab did not become active within \(Int(waitSeconds))s "
               + "(heading '\(lastObserved ?? "nil")', tab button '\(buttonState)')",
        observed: lastObserved)
}

/// Waits until the row count stops changing, so we don't read a half-populated list.
func settle(pid: pid_t, maxMilliseconds: Int) {
    let deadline = Date().addingTimeInterval(Double(maxMilliseconds) / 1000.0)
    var previous = rowCount(pid: pid)
    while Date() < deadline {
        Thread.sleep(forTimeInterval: 0.3)
        let current = rowCount(pid: pid)
        if current == previous && current >= 0 { return }
        previous = current
    }
}

// MARK: - App lifecycle

func findMyProcess() -> NSRunningApplication? {
    NSWorkspace.shared.runningApplications.first { $0.bundleIdentifier == findMyBundleID }
}

/// Asks the system to open Find My. Used both to launch it and to bring back its
/// window: Find My frequently sits running with zero AX windows (the state
/// imac/fix_findmy_window.sh existed to repair), and re-opening recreates the scene.
///
/// Launches by bundle identifier — the app's CFBundleName became "FindMy" (no space)
/// in Find My 5.0, so launching by name, as this tool used to, no longer works.
@discardableResult
func openFindMy(activates: Bool, timeout: Double = 20, attempts: Int = 3) -> NSRunningApplication? {
    guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: findMyBundleID) else {
        return nil
    }
    let config = NSWorkspace.OpenConfiguration()
    config.activates = activates
    config.addsToRecentItems = false

    // Opening an app that is still shutting down fails outright, and the caller's
    // usual recovery is exactly that — pkill Find My, then ask us to start it again.
    // So retry rather than reporting "not running" on the first refusal.
    for attempt in 0..<attempts {
        var launched: NSRunningApplication?
        var openError: Error?
        let semaphore = DispatchSemaphore(value: 0)
        NSWorkspace.shared.openApplication(at: url, configuration: config) { app, error in
            // Take the app from the callback rather than polling
            // NSWorkspace.runningApplications: that list is refreshed by workspace
            // notifications delivered on the main run loop, which a command-line
            // tool never runs, so a freshly launched app would stay invisible to it.
            launched = app
            openError = error
            semaphore.signal()
        }
        guard semaphore.wait(timeout: .now() + timeout) == .success else {
            warn("openApplication attempt \(attempt + 1)/\(attempts) timed out")
            continue
        }

        if let launched {
            // Give the Catalyst scene a moment to build its AX tree.
            Thread.sleep(forTimeInterval: 2.0)
            return launched
        }
        warn("openApplication attempt \(attempt + 1)/\(attempts) failed: "
             + (openError?.localizedDescription ?? "no application returned"))
        if attempt < attempts - 1 { Thread.sleep(forTimeInterval: 2.0) }
    }
    return nil
}

// MARK: - Output

func iso8601Now() -> String {
    let formatter = ISO8601DateFormatter()
    formatter.timeZone = TimeZone(identifier: "UTC")
    return formatter.string(from: Date())
}

func makeEncoder(pretty: Bool, snakeCase: Bool) -> JSONEncoder {
    let encoder = JSONEncoder()
    var formatting: JSONEncoder.OutputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    if pretty { formatting.insert(.prettyPrinted) }
    encoder.outputFormatting = formatting
    if snakeCase { encoder.keyEncodingStrategy = .convertToSnakeCase }
    return encoder
}

func emit<T: Encodable>(_ payload: T, pretty: Bool, snakeCase: Bool) -> Bool {
    do {
        let data = try makeEncoder(pretty: pretty, snakeCase: snakeCase).encode(payload)
        // Swift omits keys whose value is nil rather than emitting null, so callers
        // must use .get()-style access for every optional field.
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write("\n".data(using: .utf8)!)
        return true
    } catch {
        warn("JSON encoding failed: \(error)")
        return false
    }
}

/// Failure still prints a JSON object, so a caller that only captured stdout can
/// report *why*. The exit code is the signal; this is the detail. Never prints "[]".
func fail(_ code: ExitCode, _ message: String, tabRequested: String? = nil,
          tabObserved: String? = nil, pid: pid_t? = nil, pretty: Bool) -> Never {
    warn("error [\(code.kind)]: \(message)")
    let envelope = Envelope(
        ok: false,
        schemaVersion: schemaVersion,
        tab: nil,
        tabRequested: tabRequested,
        tabVerified: false,
        extractedAt: iso8601Now(),
        appPid: pid,
        count: 0,
        warnings: [],
        devices: [],
        error: ExtractionError(code: code.rawValue, kind: code.kind, message: message,
                               tabRequested: tabRequested, tabObserved: tabObserved)
    )
    _ = emit(envelope, pretty: pretty, snakeCase: true)
    exit(code.rawValue)
}

// MARK: - CLI

let usageText = """
Usage: airtag_extractor [options]

  --tab NAME            people | devices | items | me
                        Switches to the tab and verifies the switch landed.
                        Omit to read whichever tab is currently active.
                        Switching brings Find My to the front — the press is
                        silently ignored otherwise.
  --wait-for-tab SECS   How long to wait for the tab switch (default 20)
  --settle-ms MS        Wait for the row count to stabilise, up to MS (default 800)
  --launch              Launch Find My if it isn't running, and reopen its window
                        if it is running without one
  --no-activate         Never bring Find My to the front. Reading the current tab
                        still works; switching tabs will not.
  --require-idle SECS   Do nothing that steals focus unless the Mac has been idle
                        for at least SECS. Exits 8 (user_active) instead, without
                        touching Find My. 0 (default) disables the check.
  --print-idle          Print seconds since the last user input and exit
  --request-permission  Ask macOS for Accessibility permission, which registers the
                        calling process in System Settings > Privacy & Security >
                        Accessibility so it can be switched on. Run this from the
                        context that needs the grant (the LaunchAgent, not a shell).
  --details             After reading the list, select each row and harvest its
                        street-level address (the list alone shows only "Ghent";
                        the selected row exposes "Kortrijksesteenweg, Ghent").
                        Adds ~0.3s per row and needs Find My frontmost.
  --include-raw         Include each row's raw text array
  --format FORMAT       json (default) | legacy
  --pretty              Pretty-print the JSON
  --version, --help

Exit codes:
  0 ok            2 ax_not_trusted   3 app_not_running   4 no_window
  5 tab_switch_failed                6 no_rows           7 ax_error
  8 user_active   64 usage
"""

struct Options {
    var tab: String?
    var waitForTab: Double = 20
    var settleMs: Int = 800
    var launch = false
    var includeRaw = false
    var legacyFormat = false
    var pretty = false
    var noActivate = false
    var requireIdle: Double = 0
    var printIdle = false
    var requestPermission = false
    var details = false
}

func parseArguments() -> Options {
    var options = Options()
    var args = Array(CommandLine.arguments.dropFirst())

    func next(_ flag: String) -> String {
        guard !args.isEmpty else {
            warn("\(flag) requires a value\n\n\(usageText)")
            exit(ExitCode.usage.rawValue)
        }
        return args.removeFirst()
    }

    let validTabs = ["people": "People", "devices": "Devices", "items": "Items", "me": "Me"]

    while !args.isEmpty {
        let arg = args.removeFirst()
        switch arg {
        case "--tab":
            let raw = next("--tab").lowercased()
            guard let canonical = validTabs[raw] else {
                warn("unknown tab '\(raw)'; expected one of: people, devices, items, me")
                exit(ExitCode.usage.rawValue)
            }
            options.tab = canonical
        case "--wait-for-tab":
            options.waitForTab = Double(next("--wait-for-tab")) ?? 20
        case "--settle-ms":
            options.settleMs = min(max(Int(next("--settle-ms")) ?? 800, 0), 5000)
        case "--launch":
            options.launch = true
        case "--require-idle":
            options.requireIdle = max(Double(next("--require-idle")) ?? 0, 0)
        case "--print-idle":
            options.printIdle = true
        case "--request-permission":
            options.requestPermission = true
        case "--no-activate":
            options.noActivate = true
        case "--details":
            options.details = true
        case "--include-raw":
            options.includeRaw = true
        case "--format":
            let value = next("--format").lowercased()
            guard value == "json" || value == "legacy" else {
                warn("unknown format '\(value)'; expected json or legacy")
                exit(ExitCode.usage.rawValue)
            }
            options.legacyFormat = value == "legacy"
        case "--pretty":
            options.pretty = true
        case "--version":
            print("airtag_extractor schema \(schemaVersion)")
            exit(0)
        case "-h", "--help":
            print(usageText)
            exit(0)
        default:
            warn("unknown argument '\(arg)'\n\n\(usageText)")
            exit(ExitCode.usage.rawValue)
        }
    }
    return options
}

// MARK: - Main

func run() -> Never {
    let options = parseArguments()

    if options.printIdle {
        print(String(format: "%.1f", userIdleSeconds()))
        exit(0)
    }

    // Asking with the prompt option is what makes macOS list the *responsible* process
    // in System Settings, which for a LaunchAgent is the agent's program rather than
    // this binary. Without an explicit ask, a launchd process is simply denied in
    // silence: nothing is ever prompted and nothing appears in the list to switch on.
    if options.requestPermission {
        let trusted = AXIsProcessTrustedWithOptions(
            [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary)
        if trusted {
            print("Accessibility permission is already granted for this process.")
            exit(0)
        }
        print("Requested Accessibility permission. Approve it under System Settings > "
              + "Privacy & Security > Accessibility, then run this again to confirm.")
        exit(ExitCode.axNotTrusted.rawValue)
    }

    // Stand down before anything else if someone is using the Mac. Checked here, ahead
    // of the AX trust check and ahead of launching Find My, because every step past
    // this point can pull focus away from them.
    if options.requireIdle > 0 {
        let idle = userIdleSeconds()
        if idle < options.requireIdle {
            fail(.userActive,
                 String(format: "Mac in use — %.0fs since last input, need %.0fs idle",
                        idle, options.requireIdle),
                 tabRequested: options.tab, pretty: options.pretty)
        }
    }

    // Checked before touching any AX API: without this the old binary silently
    // reported "no devices" when the real problem was a revoked TCC grant.
    guard AXIsProcessTrusted() else {
        fail(.axNotTrusted,
             "Accessibility permission is not granted. Add this binary's parent process "
             + "(Terminal, iTerm, or the LaunchAgent's program) under System Settings > "
             + "Privacy & Security > Accessibility.",
             pretty: options.pretty)
    }

    var app = findMyProcess()
    if app == nil {
        guard options.launch else {
            fail(.appNotRunning, "Find My is not running (pass --launch to start it)", pretty: options.pretty)
        }
        // Activation is not cosmetic here: Find My only builds its window scene when
        // it is brought to the front. Launched in the background it sits there with
        // zero accessible windows and nothing to read.
        app = openFindMy(activates: !options.noActivate)
        guard app != nil else {
            fail(.appNotRunning, "failed to launch Find My (\(findMyBundleID))", pretty: options.pretty)
        }
    }
    var pid = app!.processIdentifier

    // Confirm there is a window to work with BEFORE trying to switch tabs. Find My
    // is often running with zero AX windows, and a tab switch attempted in that
    // state fails in a way that looks like a tab problem — which sends the caller
    // down a retry path instead of the window-recovery path it actually needs.
    if findCardContainerWithRetry(pid: pid) == nil, options.launch {
        warn("Find My has no accessible window; re-opening to recreate it")
        if let reopened = openFindMy(activates: !options.noActivate) {
            app = reopened
            pid = reopened.processIdentifier
        }
    }
    // A lingering detail view (opened by a stray double-click on a row) also presents
    // as "no CardContainerView", but is fixed by a View-menu press, not by reopening
    // the window. Try that first — it is free and needs no focus theatrics.
    if findCardContainerWithRetry(pid: pid) == nil,
       let anyTab = viewMenuTabItems(pid: pid).first?.element {
        warn("no CardContainerView; pressing a View-menu tab in case a detail view is open")
        AXUIElementPerformAction(anyTab, kAXPressAction as CFString)
        Thread.sleep(forTimeInterval: 1.0)
    }
    guard findCardContainerWithRetry(pid: pid) != nil else {
        fail(.noWindow,
             "Find My is running but has no accessible window containing CardContainerView "
             + "(the window is closed or minimised)"
             + (options.launch ? "" : "; pass --launch to let this tool reopen it"),
             tabRequested: options.tab, pid: pid, pretty: options.pretty)
    }

    var tabVerified = false
    var observedTab: String?

    if let target = options.tab {
        switch switchToTab(app: app!, target: target, waitSeconds: options.waitForTab,
                           activate: !options.noActivate, requireIdle: options.requireIdle) {
        case .ok(let observed):
            tabVerified = true
            observedTab = observed ?? target
        case .userActive(let idle):
            fail(.userActive,
                 String(format: "Mac came into use while preparing the switch — "
                        + "%.0fs since last input, need %.0fs idle", idle, options.requireIdle),
                 tabRequested: target, pid: pid, pretty: options.pretty)
        case .failed(let message, let observed):
            // A window that vanished mid-switch is a window problem, not a tab one.
            let code: ExitCode = findCardContainer(pid: pid) == nil ? .noWindow : .tabSwitchFailed
            fail(code, message, tabRequested: target, tabObserved: observed,
                 pid: pid, pretty: options.pretty)
        }
        settle(pid: pid, maxMilliseconds: options.settleMs)
    }

    guard let card = findCardContainerWithRetry(pid: pid) else {
        fail(.noWindow,
             "Find My's window disappeared between the tab switch and the read",
             tabRequested: options.tab, pid: pid, pretty: options.pretty)
    }

    if observedTab == nil { observedTab = activeTabName(card) }

    var rowGroups: [[AXUIElement]] = []
    scanRows(card, into: &rowGroups)

    // The tree was rebuilt mid-scan; one retry, then give up rather than report
    // a partial list as if it were complete.
    if sawStaleElement {
        sawStaleElement = false
        rowGroups = []
        guard let fresh = findCardContainerWithRetry(pid: pid) else {
            fail(.axError, "accessibility tree changed during scan and could not be re-read",
                 tabRequested: options.tab, pid: pid, pretty: options.pretty)
        }
        scanRows(fresh, into: &rowGroups)
        if sawStaleElement {
            fail(.axError, "accessibility tree kept changing during scan",
                 tabRequested: options.tab, pid: pid, pretty: options.pretty)
        }
    }

    var devices: [DeviceRow] = []
    var warnings: [String] = []
    for (index, children) in rowGroups.enumerated() {
        guard let parsed = parseRow(children: children, index: index, includeRaw: options.includeRaw) else {
            warnings.append("row_\(index)_unparsed")
            continue
        }
        devices.append(parsed.row)
        warnings.append(contentsOf: parsed.warnings)
    }

    // Street addresses come from a second pass that SELECTS each row: the plain list
    // shows "Ghent", the selected row shows "Kortrijksesteenweg, Ghent". Run it after
    // parsing — parseRow has already copied every text out, so the tree churn the
    // presses cause cannot corrupt the rows above.
    if options.details, !devices.isEmpty {
        if options.noActivate {
            warnings.append("details_skipped_no_activate")
        } else {
            app!.activate()
            Thread.sleep(forTimeInterval: 0.4)
            currentDetailsTab = observedTab ?? options.tab ?? "Items"
            for enriched in sweepRowDetails(pid: pid) {
                // Attach by name — the list re-sorts itself live, so positions mean
                // nothing. Duplicate names each claim the first same-named row that
                // has no address yet.
                guard let name = enriched.components(separatedBy: ", ").first,
                      let deviceIndex = devices.firstIndex(where: {
                          $0.name == name && $0.address == nil
                      }) else { continue }
                devices[deviceIndex].address = parseEnrichedAddress(enriched)
            }
        }
    }

    let extractedAt = iso8601Now()

    if options.legacyFormat {
        let legacy = devices.map {
            LegacyDevice(name: $0.name,
                         location: $0.location ?? "-",
                         timeStatus: $0.timeStatus ?? "-",
                         distance: $0.distance?.text ?? "-",
                         batteryStatus: $0.battery,
                         extractedAt: extractedAt)
        }
        guard emit(legacy, pretty: options.pretty, snakeCase: false) else {
            exit(ExitCode.axError.rawValue)
        }
    } else {
        let envelope = Envelope(
            ok: true,
            schemaVersion: schemaVersion,
            tab: observedTab,
            tabRequested: options.tab,
            tabVerified: tabVerified,
            extractedAt: extractedAt,
            appPid: pid,
            count: devices.count,
            warnings: warnings,
            devices: devices,
            error: nil
        )
        guard emit(envelope, pretty: options.pretty, snakeCase: true) else {
            exit(ExitCode.axError.rawValue)
        }
    }

    warn("extracted \(devices.count) row(s) from tab '\(observedTab ?? "unknown")'")

    // An empty list on a verified tab is legitimate (the Me tab has no rows), but
    // it is still worth distinguishing from a successful read with content.
    exit(devices.isEmpty ? ExitCode.noRows.rawValue : ExitCode.ok.rawValue)
}

run()
