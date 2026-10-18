#if os(macOS)
    import AppKit
    import LachingShell
    import SwiftUI

    extension ScreenshotCapture {
        // MARK: Capture

        /// Draws the whole window (title bar included) once it is key (unless `isHidden`), so it looks as it does in use,
        /// then checks that its content fits (`LayoutCheck`). A hidden window waits as long as a key one does after
        /// coming forward, so its first tasks (a scroll to a row) land before it is drawn.
        public static func capture(_ window: NSWindow, to url: URL) async {
            let shot = url.lastPathComponent
            await makeKey(window, for: shot)
            if isHidden { try? await Task.sleep(for: .seconds(0.5)) }
            guard let view = window.contentView?.superview ?? window.contentView,
                let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds)
            else { return report("\(shot): the window couldn't be drawn") }
            window.makeFirstResponder(nil)  // no focus ring in the shot
            view.displayIfNeeded()
            view.cacheDisplay(in: view.bounds, to: rep)
            write(rep, to: url)
            if let overflow = LayoutCheck.overflow(in: window) { report("\(shot): \(overflow)") }
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
        /// to open in (a later onboarding page).
        public static func captureWindow(_ view: some View, size: NSSize, background: NSColor, to url: URL) async {
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
            await capture(window, to: url)
            window.close()
        }

        /// `view` on its own (a panel, a sheet of poses) at its fitting size.
        public static func captureFitting(_ view: some View, to url: URL) async {
            let host = NSHostingView(rootView: view)
            host.frame.size = host.fittingSize
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false  // ARC owns it; AppKit's extra release on close crashes
            window.contentView = host
            try? await Task.sleep(for: .seconds(0.5))
            if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                host.cacheDisplay(in: host.bounds, to: rep)
                write(rep, to: url)
            } else {
                report("\(url.lastPathComponent): the view couldn't be drawn")
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
                if NSApp.isActive && window.isKeyWindow { try? await Task.sleep(for: .seconds(0.5)) }
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
