// set_display_mode.swift — set the resolution of the virtual (DeskPad) display.
//
// Why this exists: Find My is a Catalyst app and will not build a window scene unless
// the GUI session has a display to render on. On this headless-ish Mac that display is
// DeskPad's virtual one, which comes up at 3840x2160 (1920x1080 logical) — far more
// than is needed for a screen nobody looks at. DeskPad 1.3.2 offers no resolution
// setting of its own and ignores its window size, so the mode is set here instead.
//
//   set_display_mode --list                 show every mode of the target display
//   set_display_mode --width 1280 --height 800
//   set_display_mode --width 1280 --height 800 --display 17
//
// Exit codes: 0 ok, 1 no target display, 2 mode not available, 3 set failed, 64 usage.
//
// Safety: with no --display, this refuses to act when more than one external display is
// online. Guessing would mean resizing a real monitor someone is using.

import Foundation
import CoreGraphics

func onlineDisplays() -> [CGDirectDisplayID] {
    var count: UInt32 = 0
    CGGetOnlineDisplayList(0, nil, &count)
    var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
    CGGetOnlineDisplayList(count, &ids, &count)
    return Array(ids.prefix(Int(count)))
}

func modes(_ id: CGDirectDisplayID) -> [CGDisplayMode] {
    let opts = [kCGDisplayShowDuplicateLowResolutionModes as String: true] as CFDictionary
    return (CGDisplayCopyAllDisplayModes(id, opts) as? [CGDisplayMode]) ?? []
}

var wantWidth: Int?
var wantHeight: Int?
var explicitDisplay: CGDirectDisplayID?
var listOnly = false
var wantHiDPI = false

var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let arg = args.removeFirst()
    func value(_ flag: String) -> String {
        guard !args.isEmpty else {
            FileHandle.standardError.write("\(flag) requires a value\n".data(using: .utf8)!)
            exit(64)
        }
        return args.removeFirst()
    }
    switch arg {
    case "--width":   wantWidth = Int(value("--width"))
    case "--height":  wantHeight = Int(value("--height"))
    case "--display": explicitDisplay = UInt32(value("--display"))
    case "--list":    listOnly = true
    case "--hidpi":   wantHiDPI = true
    case "-h", "--help":
        print("usage: set_display_mode [--list] [--width W --height H] [--hidpi] [--display ID]")
        print("  --width/--height are the logical size; the 1:1 mode is chosen unless --hidpi")
        exit(0)
    default:
        FileHandle.standardError.write("unknown argument '\(arg)'\n".data(using: .utf8)!)
        exit(64)
    }
}

let external = onlineDisplays().filter { CGDisplayIsBuiltin($0) == 0 }

let target: CGDirectDisplayID
if let explicitDisplay {
    target = explicitDisplay
} else if external.count == 1 {
    target = external[0]
} else if external.isEmpty {
    FileHandle.standardError.write("no external display online (is DeskPad running?)\n".data(using: .utf8)!)
    exit(1)
} else {
    let ids = external.map(String.init).joined(separator: ", ")
    let message = "\(external.count) external displays online (\(ids)); pass --display to say "
        + "which one, rather than resizing a monitor someone is using\n"
    FileHandle.standardError.write(message.data(using: .utf8)!)
    exit(1)
}

if listOnly {
    print("display \(target): currently \(CGDisplayPixelsWide(target))x\(CGDisplayPixelsHigh(target))")
    for m in modes(target).sorted(by: { $0.pixelWidth * $0.pixelHeight < $1.pixelWidth * $1.pixelHeight }) {
        print("  \(m.width)x\(m.height) pixels=\(m.pixelWidth)x\(m.pixelHeight) usable=\(m.isUsableForDesktopGUI())")
    }
    exit(0)
}

guard let wantWidth, let wantHeight else {
    FileHandle.standardError.write("--width and --height are required (or --list)\n".data(using: .utf8)!)
    exit(64)
}

let current = CGDisplayCopyDisplayMode(target)
if current?.width == wantWidth, current?.height == wantHeight,
   wantHiDPI == ((current?.pixelWidth ?? 0) > (current?.width ?? 0)) {
    print("display \(target) already \(wantWidth)x\(wantHeight)\(wantHiDPI ? " HiDPI" : "")")
    exit(0)
}

// --width/--height are the LOGICAL size, i.e. what macOS reports as "UI looks like".
// Two modes can share it: a 1:1 one and a HiDPI one with a 2x backing store. They are
// not interchangeable — 1920x1200 HiDPI allocates 3840x2400 pixels, which is exactly
// the memory this is meant to avoid — so pick the 1:1 mode unless asked otherwise.
let matching = modes(target).filter {
    $0.width == wantWidth && $0.height == wantHeight && $0.isUsableForDesktopGUI()
}
let mode1to1 = matching.first { $0.pixelWidth == $0.width && $0.pixelHeight == $0.height }
guard let mode = wantHiDPI ? (matching.first { $0.pixelWidth > $0.width } ?? mode1to1) : (mode1to1 ?? matching.first) else {
    FileHandle.standardError.write(
        "no usable \(wantWidth)x\(wantHeight) mode on display \(target); try --list\n".data(using: .utf8)!)
    exit(2)
}

var config: CGDisplayConfigRef?
guard CGBeginDisplayConfiguration(&config) == .success else {
    FileHandle.standardError.write("could not begin display configuration\n".data(using: .utf8)!)
    exit(3)
}
CGConfigureDisplayWithDisplayMode(config, target, mode, nil)
guard CGCompleteDisplayConfiguration(config, .permanently) == .success else {
    FileHandle.standardError.write("could not apply display configuration\n".data(using: .utf8)!)
    exit(3)
}

Thread.sleep(forTimeInterval: 1.5)
let now = CGDisplayCopyDisplayMode(target)
print("display \(target) set to logical \(now?.width ?? 0)x\(now?.height ?? 0), "
      + "pixels \(now?.pixelWidth ?? 0)x\(now?.pixelHeight ?? 0)")
