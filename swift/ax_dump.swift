// ax_dump.swift — print Find My's accessibility tree.
//
// This is the tool to reach for when Find My changes and the extractor stops
// finding rows. Run it directly, no build step:
//
//     swift swift/ax_dump.swift              # Find My, whichever tab is open
//     swift swift/ax_dump.swift --frontmost  # whatever app is in front instead
//     swift swift/ax_dump.swift --depth 12   # limit how deep it prints
//
// It prints role, subrole, title, description, value and identifier for every
// element, because which of those carries the data has changed between releases:
// as of Find My 5.0 row text lives in AXDescription, and AXIdentifier
// ("CardContainerView", "ListEntityRow", "PrimaryLabel") is what makes structural
// navigation possible.
//
// Note the map subtree is large and mostly noise — hundreds of AXMapItem points of
// interest. Pipe through `grep ListEntityRow` to see just the rows.

import Foundation
import ApplicationServices
import AppKit

var maxDepth = 40
var useFrontmost = false

var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let arg = args.removeFirst()
    switch arg {
    case "--frontmost":
        useFrontmost = true
    case "--depth":
        if let value = args.first, let parsed = Int(value) { maxDepth = parsed; args.removeFirst() }
    case "-h", "--help":
        print("usage: swift ax_dump.swift [--frontmost] [--depth N]")
        exit(0)
    default:
        FileHandle.standardError.write("unknown argument '\(arg)'\n".data(using: .utf8)!)
        exit(64)
    }
}

guard AXIsProcessTrusted() else {
    FileHandle.standardError.write(
        "Accessibility permission is not granted for this process. Add your terminal under\n"
        .data(using: .utf8)!)
    FileHandle.standardError.write(
        "System Settings > Privacy & Security > Accessibility.\n".data(using: .utf8)!)
    exit(2)
}

func attribute(_ element: AXUIElement, _ name: String) -> String? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success,
          let value else { return nil }
    if let string = value as? String { return string.isEmpty ? nil : string }
    if let number = value as? NSNumber { return number.stringValue }
    return nil
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value) == .success
    else { return [] }
    return value as? [AXUIElement] ?? []
}

func dump(_ element: AXUIElement, depth: Int) {
    if depth > maxDepth { return }

    var parts = [attribute(element, kAXRoleAttribute as String) ?? "?"]
    for (label, key) in [("sub", kAXSubroleAttribute as String),
                         ("title", kAXTitleAttribute as String),
                         ("desc", kAXDescriptionAttribute as String),
                         ("value", kAXValueAttribute as String),
                         ("id", kAXIdentifierAttribute as String)] {
        if let value = attribute(element, key) { parts.append("\(label)=\"\(value)\"") }
    }

    print(String(repeating: "  ", count: depth) + "[\(depth)] " + parts.joined(separator: " "))
    for child in children(element) { dump(child, depth: depth + 1) }
}

let app: NSRunningApplication?
if useFrontmost {
    app = NSWorkspace.shared.frontmostApplication
} else {
    app = NSWorkspace.shared.runningApplications.first { $0.bundleIdentifier == "com.apple.findmy" }
}

guard let app else {
    FileHandle.standardError.write(
        (useFrontmost ? "No frontmost application.\n" : "Find My is not running.\n")
            .data(using: .utf8)!)
    exit(3)
}

print("app: \(app.localizedName ?? "?") (\(app.bundleIdentifier ?? "?")) pid=\(app.processIdentifier)")

var windowValue: CFTypeRef?
AXUIElementCopyAttributeValue(AXUIElementCreateApplication(app.processIdentifier),
                              kAXWindowsAttribute as CFString, &windowValue)
let windows = windowValue as? [AXUIElement] ?? []

// Zero windows is a real state, not an error: Find My sits running without a
// window scene until something activates it.
print("windows: \(windows.count)")
for (index, window) in windows.enumerated() {
    print("--- window \(index) ---")
    dump(window, depth: 0)
}
