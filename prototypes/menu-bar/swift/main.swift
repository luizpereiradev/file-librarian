// PROTOTYPE, NOT SHIPPED. Spike for issue #111; see docs/decisions/0004-menu-bar-item.md.
//
// A menu bar item that shows flib's state from a small JSON file and offers a few
// actions. Compiled at install time with the Command Line Tools:
//
//   swiftc -O -framework AppKit main.swift -o <app>/Contents/MacOS/<name>
//
// Contract with flib (what #116 builds on):
//   - flib writes <state dir>/state.json atomically (write a temp file, rename);
//     {"pose": "asleep|busy|due|attention|trouble", "count": 3, "line": "3 to review"}
//   - the app watches the folder (kqueue via DispatchSource: no polling), re-reads
//     on a slow timer and after the Mac wakes, and never parses anything else;
//   - icons are template SVGs in Contents/Resources/<pose>.svg, tinted by macOS;
//   - a menu click runs a fixed command with fixed arguments, never a shell string.
//
// The state folder comes from the bundle's Info.plist (FlibMenuStateDir), written
// by the build, so the app finds it whether launchd or `open` starts it.
//
// The self-test hook (a `selftest.request` file in the state folder) exists only to
// measure the spike: it renders the button in light and dark appearance and fires a
// menu item through NSMenu, then writes selftest.json.

import AppKit

final class MenuApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private let line = NSMenuItem(title: "…", action: nil, keyEquivalent: "")
    private var watcher: DispatchSourceFileSystemObject?
    private var timer: Timer?
    private var lastState: Data?
    private var refreshes = 0
    private let stateDir: URL

    override init() {
        let info = Bundle.main.infoDictionary ?? [:]
        let fallback = NSHomeDirectory() + "/Library/Application Support/io.flibtest.menu/state"
        stateDir = URL(fileURLWithPath: (info["FlibMenuStateDir"] as? String) ?? fallback)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // One item only: a second copy (a double launch, launchd plus `open`) quits.
        let me = NSRunningApplication.current
        let others = NSRunningApplication.runningApplications(
            withBundleIdentifier: Bundle.main.bundleIdentifier ?? ""
        ).filter { $0.processIdentifier != me.processIdentifier }
        if !others.isEmpty {
            log("another copy is running (pid \(others[0].processIdentifier)); quitting")
            NSApp.terminate(nil)
            return
        }

        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.imagePosition = .imageLeading
        item.autosaveName = Bundle.main.bundleIdentifier  // keeps the user's ⌘-drag position

        line.isEnabled = false
        menu.addItem(line)
        menu.addItem(.separator())
        addAction("Organize now", #selector(organize))
        addAction("Open review folder", #selector(openReview))
        menu.addItem(.separator())
        addAction("Quit flib-menu-test", #selector(quit))
        menu.delegate = self
        item.menu = menu

        try? FileManager.default.createDirectory(at: stateDir, withIntermediateDirectories: true)
        watch()
        // A slow safety net, with a generous tolerance so macOS can batch the wakeup.
        timer = Timer.scheduledTimer(withTimeInterval: 15 * 60, repeats: true) { [weak self] _ in
            self?.refresh(force: true)
        }
        timer?.tolerance = 60
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(didWake), name: NSWorkspace.didWakeNotification, object: nil
        )
        refresh(force: true)
        log("started pid \(me.processIdentifier) parent \(getppid())")
    }

    private func addAction(_ title: String, _ action: Selector) {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
        entry.target = self
        menu.addItem(entry)
    }

    // kqueue on the folder: fires when flib renames a new state.json into place.
    private func watch() {
        let fd = open(stateDir.path, O_EVTONLY)
        guard fd >= 0 else { log("cannot watch \(stateDir.path)"); return }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd, eventMask: [.write, .rename, .delete], queue: .main
        )
        source.setEventHandler { [weak self] in self?.refresh(force: false) }
        source.setCancelHandler { close(fd) }
        source.resume()
        watcher = source
    }

    @objc private func didWake(_ note: Notification) {
        log("woke")
        refresh(force: true)
    }

    func menuWillOpen(_ menu: NSMenu) { refresh(force: false) }

    private func refresh(force: Bool) {
        if FileManager.default.fileExists(atPath: stateDir.appendingPathComponent("selftest.request").path) {
            try? FileManager.default.removeItem(at: stateDir.appendingPathComponent("selftest.request"))
            selftest()
        }
        let quitRequest = stateDir.appendingPathComponent("quit.request")
        if FileManager.default.fileExists(atPath: quitRequest.path) {
            try? FileManager.default.removeItem(at: quitRequest)
            log("quit.request: choosing Quit from the menu")
            menu.performActionForItem(at: menu.numberOfItems - 1)
        }
        let data = try? Data(contentsOf: stateDir.appendingPathComponent("state.json"))
        if !force && data == lastState { return }
        lastState = data
        refreshes += 1
        let state = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
        let pose = (state["pose"] as? String).flatMap { Self.poses.contains($0) ? $0 : nil } ?? "trouble"
        let count = state["count"] as? Int ?? 0
        let image = Bundle.main.url(forResource: pose, withExtension: "svg").flatMap(NSImage.init(contentsOf:))
        image?.isTemplate = true
        image?.size = NSSize(width: 18, height: 18)
        image?.accessibilityDescription = "flib: \(pose)"
        item.button?.image = image
        item.button?.title = count > 0 ? "\(count)" : ""
        line.title = (state["line"] as? String) ?? (data == nil ? "flib has not written its state yet" : "…")
        log("shown \(pose) \(count)")
    }

    private static let poses: Set<String> = ["asleep", "busy", "due", "attention", "trouble"]

    @objc private func organize() { run("/bin/date", ["+organize %s"]) }
    @objc private func openReview() { run("/bin/date", ["+open-review %s"]) }
    @objc private func quit() { NSApp.terminate(nil) }

    // A fixed executable and fixed arguments; output goes to actions.log.
    private func run(_ tool: String, _ args: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: tool)
        process.arguments = args
        let out = stateDir.appendingPathComponent("actions.log")
        if !FileManager.default.fileExists(atPath: out.path) {
            FileManager.default.createFile(atPath: out.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: out) {
            handle.seekToEndOfFile()
            process.standardOutput = handle
        }
        do { try process.run() } catch { log("cannot run \(tool): \(error)") }
    }

    private func log(_ text: String) {
        let out = stateDir.appendingPathComponent("app.log")
        let entry = String(format: "%.3f %@\n", Date().timeIntervalSince1970, text).data(using: .utf8)!
        if let handle = try? FileHandle(forWritingTo: out) {
            handle.seekToEndOfFile()
            handle.write(entry)
            try? handle.close()
        } else {
            try? entry.write(to: out)
        }
    }

    // ---- measurement only ----
    private func selftest() {
        guard let button = item.button else { return }
        var report: [String: Any] = [
            "pid": Int(getpid()),
            "parent": Int(getppid()),
            "activation_policy": NSApp.activationPolicy().rawValue,  // 1 = accessory
            "visible": item.isVisible,
            "effective_appearance": button.effectiveAppearance.name.rawValue,
            "image_is_template": button.image?.isTemplate ?? false,
            "title": button.title,
            "refreshes": refreshes,
        ]
        if let window = button.window {
            report["frame"] = NSStringFromRect(window.frame)
            report["on_screen"] = window.occlusionState.contains(.visible)
            report["screen"] = window.screen.map { NSStringFromRect($0.frame) } ?? "none"
        }
        for (name, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            button.appearance = NSAppearance(named: appearance)
            button.display()
            if let rep = button.bitmapImageRepForCachingDisplay(in: button.bounds) {
                button.cacheDisplay(in: button.bounds, to: rep)
                let png = rep.representation(using: .png, properties: [:])
                try? png?.write(to: stateDir.appendingPathComponent("button-\(name).png"))
                report["ink_\(name)"] = Self.ink(rep)
            }
        }
        button.appearance = nil
        // Fire "Organize now" through the menu, as a click does.
        let index = menu.indexOfItem(withTitle: "Organize now")
        menu.performActionForItem(at: index)
        report["fired"] = menu.item(at: index)?.title ?? ""
        if let json = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? json.write(to: stateDir.appendingPathComponent("selftest.json"))
        }
    }

    // Mean brightness (0-255) of the drawn pixels: dark ink on light, light ink on dark.
    private static func ink(_ rep: NSBitmapImageRep) -> Int {
        var total = 0.0, weight = 0.0
        for x in 0..<rep.pixelsWide {
            for y in 0..<rep.pixelsHigh {
                guard let c = rep.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { continue }
                let a = Double(c.alphaComponent)
                total += a * Double(c.redComponent + c.greenComponent + c.blueComponent) / 3 * 255
                weight += a
            }
        }
        return weight > 0 ? Int(total / weight) : -1
    }
}

let app = NSApplication.shared
let delegate = MenuApp()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // LSUIElement says the same; belt and braces
app.run()
