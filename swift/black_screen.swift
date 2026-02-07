import Cocoa

// ─── Click-through window ───

class BlackWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

// ─── App Delegate ───

class BlackScreenApp: NSObject, NSApplicationDelegate {
    var windows: [NSWindow] = []
    var clickCount = 0
    var lastClickTime: Date = .distantPast

    func applicationDidFinishLaunching(_ notification: Notification) {
        for screen in NSScreen.screens {
            let window = BlackWindow(
                contentRect: screen.frame,
                styleMask: .borderless,
                backing: .buffered,
                defer: false
            )
            window.backgroundColor = .black
            window.level = .screenSaver
            window.isOpaque = true
            window.hasShadow = false
            window.collectionBehavior = [.canJoinAllSpaces, .stationary]
            window.setFrame(screen.frame, display: true)
            window.ignoresMouseEvents = true
            window.orderFrontRegardless()
            windows.append(window)
        }

        // Quit via Cmd+Q
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.handleKey(event)
        }
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.handleKey(event)
            return event
        }

        // Quit via triple-click
        NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { [weak self] _ in
            self?.handleClick()
        }
    }

    func handleKey(_ event: NSEvent) {
        if event.modifierFlags.contains(.command) && event.charactersIgnoringModifiers == "q" {
            NSApplication.shared.terminate(nil)
        }
    }

    func handleClick() {
        let now = Date()
        if now.timeIntervalSince(lastClickTime) < 0.5 {
            clickCount += 1
        } else {
            clickCount = 1
        }
        lastClickTime = now
        if clickCount >= 3 {
            NSApplication.shared.terminate(nil)
        }
    }
}

let app = NSApplication.shared
let delegate = BlackScreenApp()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
