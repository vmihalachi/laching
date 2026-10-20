#if os(iOS)
    import LachingShell
    import UIKit
    import notify

    extension ScreenshotCapture {
        // MARK: Capture

        /// The window shots are drawn from: the key window of the app's scene. Sheets, full-screen covers and alerts
        /// are presented in it, so they are in its shots; the status bar and the keyboard aren't.
        public static var keyWindow: UIWindow? {
            let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
            return windows.first(where: \.isKeyWindow) ?? windows.first
        }

        /// Shows every window of the app in light or dark (`.unspecified` follows the system again), as the system
        /// setting would, without touching the Simulator's own.
        public static func setAppearance(_ style: UIUserInterfaceStyle) {
            for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
                for window in scene.windows { window.overrideUserInterfaceStyle = style }
            }
        }

        /// `landscape` or `portrait`: the app turns that way before the first shot (`prepareWindow()`), on an iPad or an
        /// iPhone whose app allows it. `tools/check-layout-ios.py` sets it for its landscape iPad.
        public static let orientationVariable = "LACHING_SCREENSHOT_ORIENTATION"

        /// A width in points: the window narrows to it before the first shot (`prepareWindow()`), as in a Split View or
        /// Stage Manager slot, the screen's full height, compact below 678 pt (an iPad Pro 13"'s half screen).
        public static let widthVariable = "LACHING_SCREENSHOT_WIDTH"

        /// A Darwin notification name: after drawing each shot, the run prints `hold <file name>` and waits for that
        /// notification (`xcrun simctl spawn <device> notifyutil -p <name>`), so a script can take the Simulator's own
        /// screenshot of the settled screen (`xcrun simctl io <device> screenshot`), status bar included, which the
        /// drawing leaves out. Without it for `holdTimeout`, the run reports that and goes on.
        public static let holdVariable = "LACHING_SCREENSHOT_HOLD"

        /// How long a held shot waits for its notification.
        public static let holdTimeout = Duration.seconds(120)

        /// Turns and narrows the key window as `LACHING_SCREENSHOT_ORIENTATION` and `LACHING_SCREENSHOT_WIDTH` ask,
        /// then waits for it to settle; reports what the system refused. Call it once the window is up, before the
        /// first shot.
        public static func prepareWindow() async {
            let environment = ProcessInfo.processInfo.environment
            let clock = ContinuousClock()
            var deadline = clock.now + .seconds(5)
            // Called from launch, the scene may not have its window yet.
            while keyWindow == nil, clock.now < deadline { try? await Task.sleep(for: .milliseconds(50)) }
            guard let window = keyWindow, let scene = window.windowScene else { return }
            if let orientation = environment[orientationVariable], !orientation.isEmpty {
                let landscape = orientation == "landscape"
                var refusal: String?
                if scene.interfaceOrientation.isLandscape != landscape {
                    scene.requestGeometryUpdate(.iOS(interfaceOrientations: landscape ? .landscapeRight : .portrait)) { error in
                        refusal = error.localizedDescription
                    }
                    deadline = clock.now + .seconds(3)
                    while scene.interfaceOrientation.isLandscape != landscape, refusal == nil, clock.now < deadline {
                        try? await Task.sleep(for: .milliseconds(50))
                    }
                }
                if scene.interfaceOrientation.isLandscape != landscape {
                    if window.traitCollection.userInterfaceIdiom == .pad {
                        // iPadOS's windowed multitasking won't turn an app, and nothing turns the Simulator from the
                        // command line: the window takes the turned screen's size instead, regular both ways as turned.
                        let screen = scene.screen.bounds.size
                        let long = max(screen.width, screen.height)
                        let short = min(screen.width, screen.height)
                        window.frame = CGRect(x: 0, y: 0, width: landscape ? long : short, height: landscape ? short : long)
                    } else {
                        report("the window didn't turn to \(orientation)\(refusal.map { " (\($0))" } ?? "")")
                    }
                }
            }
            if let width = environment[widthVariable].flatMap(Double.init), width > 0 {
                window.frame = CGRect(x: 0, y: 0, width: CGFloat(width), height: window.bounds.height)
                if width < 678 { window.traitOverrides.horizontalSizeClass = .compact }
            }
            await settle(window)
        }

        /// Draws `window` (the key window when nil) at the screen's scale once it has settled (`settle(_:timeout:)`),
        /// then checks that its content fits (`LayoutCheck.problems(in:)`). A screen still moving at the timeout, such
        /// as one with a running timer, is drawn as it is then. A window that still looks exactly like the previous
        /// shot gets up to `changeTimeout` to change first, since a slow Simulator may not have drawn the screen or
        /// theme just asked for; if it never changes, the shot is reported. Returns whether it had settled, so the
        /// next shot of a screen that never does (the same one in dark) can wait less.
        @discardableResult
        public static func capture(_ window: UIWindow? = nil, to url: URL, timeout: Duration = .seconds(4)) async -> Bool {
            let shot = url.lastPathComponent
            guard let window = window ?? keyWindow, window.bounds.width > 0 else {
                report("\(shot): there is no window to draw")
                return false
            }
            var settled = await settle(window, timeout: timeout)
            if let previous = lastShot, thumbnail(of: window) == previous.thumbnail {
                let clock = ContinuousClock()
                let deadline = clock.now + changeTimeout
                while thumbnail(of: window) == previous.thumbnail, clock.now < deadline {
                    try? await Task.sleep(for: .milliseconds(80))
                }
                if thumbnail(of: window) == previous.thumbnail {
                    report("\(shot): drawn the same as \(previous.name); the screen or theme asked for never showed")
                } else {
                    settled = await settle(window, timeout: timeout)
                }
            }
            lastShot = thumbnail(of: window).map { (shot, $0) }
            let format = UIGraphicsImageRendererFormat(for: window.traitCollection)
            format.opaque = true
            let image = UIGraphicsImageRenderer(bounds: window.bounds, format: format).image { _ in
                _ = window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
            }
            do {
                guard let png = image.pngData() else { throw CocoaError(.fileWriteUnknown) }
                try png.write(to: url)
            } catch {
                report("\(shot): not written (\(error.localizedDescription))")
            }
            for problem in LayoutCheck.problems(in: window) { report("\(shot): \(problem)") }
            if let name = ProcessInfo.processInfo.environment[holdVariable], !name.isEmpty {
                await hold(shot, until: name)
            }
            return settled
        }

        /// Prints `hold <shot>` once it listens for the notification `name`, then waits for it (`holdVariable`).
        private static func hold(_ shot: String, until name: String) async {
            let released = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                let once = Once(continuation)
                var token: Int32 = 0
                guard notify_register_dispatch(name, &token, .main, { _ in once.resume(true) }) == NOTIFY_STATUS_OK else {
                    return once.resume(false)
                }
                once.token = token
                print("hold \(shot)")
                fflush(stdout)
                DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(Int(holdTimeout.components.seconds))) {
                    once.resume(false)
                }
            }
            if !released { report("\(shot): nothing released the hold (\(name)) in \(holdTimeout)") }
        }

        /// The first of the notification and the timeout resumes the hold and stops listening; the other does nothing,
        /// so a later hold's registration (which may reuse the token) is left alone. Used on the main queue only.
        private final class Once: @unchecked Sendable {
            private var continuation: CheckedContinuation<Bool, Never>?
            var token: Int32?

            init(_ continuation: CheckedContinuation<Bool, Never>) {
                self.continuation = continuation
            }

            func resume(_ released: Bool) {
                guard let continuation else { return }
                self.continuation = nil
                if let token { notify_cancel(token) }
                continuation.resume(returning: released)
            }
        }

        /// How long a shot that looks exactly like the previous one waits for the window to change (`capture`).
        public static let changeTimeout = Duration.seconds(2)

        /// The previous shot's file name and thumbnail, for `capture`'s check that the window changed in between.
        private static var lastShot: (name: String, thumbnail: Data)?

        /// Waits until `window` has stopped changing: a presentation finished sliding in, an image loaded, text laid
        /// out. It compares small drawings of the window a frame apart and returns once they stay alike for a few
        /// frames, false when they still differ at `timeout`. Waits a moment first, so a change the app has just
        /// asked for has started. Every drawing takes in the updates the app has committed but the screen may not
        /// show yet (`afterScreenUpdates`), so a starved Simulator's last frame can't pass for the new one.
        @discardableResult
        public static func settle(_ window: UIWindow, timeout: Duration = .seconds(4)) async -> Bool {
            let clock = ContinuousClock()
            let deadline = clock.now + timeout
            try? await Task.sleep(for: .milliseconds(250))
            var last: Data?
            var alike = 0
            while clock.now < deadline {
                let sample = thumbnail(of: window)
                alike = sample != nil && sample == last ? alike + 1 : 0
                if alike >= 3 { return true }
                last = sample
                try? await Task.sleep(for: .milliseconds(80))
            }
            return false
        }

        /// The window drawn a quarter of its size, as raw pixels to compare.
        private static func thumbnail(of window: UIWindow) -> Data? {
            let size = CGSize(width: (window.bounds.width / 4).rounded(), height: (window.bounds.height / 4).rounded())
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            format.opaque = true
            let image = UIGraphicsImageRenderer(size: size, format: format).image { _ in
                _ = window.drawHierarchy(in: CGRect(origin: .zero, size: size), afterScreenUpdates: true)
            }
            return image.cgImage?.dataProvider?.data as Data?
        }
    }
#endif
