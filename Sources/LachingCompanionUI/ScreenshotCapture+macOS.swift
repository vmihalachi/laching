#if os(macOS)
    import AppKit
    import LachingShell
    import SwiftUI

    extension ScreenshotCapture {
        // MARK: Appearances

        /// The appearances a run shoots, in order (`themes`): light, then dark, as `NSAppearance` names.
        public static var appearances: [(name: String, appearance: NSAppearance.Name)] {
            themes.map { ($0, $0 == "dark" ? NSAppearance.Name.darkAqua : .aqua) }
        }

        /// Switches the app, its open windows included, to `appearance`; they redraw, which `settle` waits for.
        public static func apply(_ appearance: NSAppearance.Name) {
            NSApplication.shared.appearance = NSAppearance(named: appearance)
        }

        // MARK: Capture

        /// Waits for `window` to be drawn and still, as iOS's `settle` does: draws its content every 100 ms (the first
        /// after 250 ms, so a view's first tasks, a scroll to a row, land) until three drawings in a row are alike, and
        /// returns the last. Nil when the window can't be drawn. Content that keeps moving gets `timeout`, then what it
        /// drew last.
        public static func settle(_ window: NSWindow, timeout: Duration = .seconds(5)) async -> NSBitmapImageRep? {
            guard let view = window.contentView?.superview ?? window.contentView else { return nil }
            let clock = ContinuousClock()
            let deadline = clock.now + timeout
            try? await Task.sleep(for: .milliseconds(250))
            var last: NSBitmapImageRep?
            var alike = 0
            while true {
                view.displayIfNeeded()
                guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return nil }
                view.cacheDisplay(in: view.bounds, to: rep)
                alike = last.map { same($0, rep) } == true ? alike + 1 : 0
                last = rep
                if alike >= 2 || clock.now >= deadline { return rep }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }

        /// Whether two drawings have the same pixels.
        private static func same(_ a: NSBitmapImageRep, _ b: NSBitmapImageRep) -> Bool {
            guard a.pixelsWide == b.pixelsWide, a.pixelsHigh == b.pixelsHigh, a.bytesPerRow == b.bytesPerRow,
                let aData = a.bitmapData, let bData = b.bitmapData
            else { return false }
            return memcmp(aData, bData, a.bytesPerRow * a.pixelsHigh) == 0
        }

        /// Draws the whole window (title bar included) once it is key (unless `isHidden`) and still (`settle`), so it
        /// looks as it does in use, then checks that its content fits (`LayoutCheck`).
        public static func capture(_ window: NSWindow, to url: URL) async {
            let shot = url.lastPathComponent
            await makeKey(window, for: shot)
            window.makeFirstResponder(nil)  // no focus ring in the shot
            guard let rep = await settle(window) else { return report("\(shot): the window couldn't be drawn") }
            write(rep, to: url)
            if let overflow = LayoutCheck.overflow(in: window) { report("\(shot): \(overflow)") }
        }

        /// The window in each appearance of `shots`, from one opening: light, then dark, each once it has redrawn.
        public static func capture(_ window: NSWindow, to shots: [(appearance: NSAppearance.Name, url: URL)]) async {
            for shot in shots {
                apply(shot.appearance)
                await capture(window, to: shot.url)
            }
        }

        /// Saves a drawn shot, reporting it when that fails.
        private static func write(_ rep: NSBitmapImageRep, to url: URL) {
            do {
                guard let png = rep.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
                try png.write(to: url)
            } catch {
                report("\(url.lastPathComponent): not written (\(error.localizedDescription))")
            }
        }

        /// `view` in a titled window built the way `WindowManager` builds one, for states a real window can't be told
        /// to open in (a later onboarding page), in each appearance of `shots`.
        public static func captureWindow(
            _ view: some View, size: NSSize, background: NSColor, to shots: [(appearance: NSAppearance.Name, url: URL)]
        ) async {
            let window = NSWindow(
                contentRect: NSRect(origin: .zero, size: size),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered,
                defer: false)
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.backgroundColor = background
            window.isReleasedWhenClosed = false  // ARC owns it; AppKit's extra release on close crashes
            window.contentViewController = NSHostingController(rootView: view)
            window.setContentSize(size)
            window.center()
            await capture(window, to: shots)
            window.close()
        }

        /// `view` on its own (a panel, a sheet of poses) at its fitting size, in each appearance of `shots`.
        public static func captureFitting(_ view: some View, to shots: [(appearance: NSAppearance.Name, url: URL)]) async {
            let host = NSHostingView(rootView: view)
            host.frame.size = host.fittingSize
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false  // ARC owns it; AppKit's extra release on close crashes
            window.contentView = host
            for shot in shots {
                apply(shot.appearance)
                if let rep = await settle(window) {
                    write(rep, to: shot.url)
                } else {
                    report("\(shot.url.lastPathComponent): the view couldn't be drawn")
                }
            }
            window.close()
        }

        /// Shots show a window key, with colored traffic lights. macOS 14+ may turn `activate()` down while another app
        /// is in front, so this asks again until the window is key, and stops the run rather than save an inactive shot.
        public static func makeKey(_ window: NSWindow, for shot: String) async {
            guard !isHidden else { return }
            var tries = 0
            while !(NSApp.isActive && window.isKeyWindow) {
                if tries == 30 {
                    FileHandle.standardError.write(
                        Data("Waiting for \(shot) to come forward. Leave the Mac alone, or click the window.\n".utf8))
                }
                if tries == 300 {
                    FileHandle.standardError.write(
                        Data("\(shot): the window never became key (another app kept the focus), so the run stopped.\n".utf8))
                    exit(1)
                }
                NSApp.activate(ignoringOtherApps: true)
                window.makeKeyAndOrderFront(nil)
                try? await Task.sleep(for: .milliseconds(100))
                tries += 1
            }
        }
    }

    /// Speaks nothing and finishes at once, so the bubble shows the line with the character awake.
    @MainActor
    public final class SilentSpeaker: Speaking {
        public var isSpeaking = false
        public var onFinished: (() -> Void)?

        public init() {}

        public func speak(_ text: String, clip: URL?, voiceIdentifier: String?, rate: Double, volume: Double) {
            onFinished?()
        }

        public func stop() {}
    }

    /// Plays nothing and completes at once.
    @MainActor
    public final class SilentChime: ChimePlaying {
        public init() {}

        public func play(_ sound: String, volume: Double, completion: (() -> Void)?) {
            completion?()
        }

        public func stop() {}
    }
#endif
