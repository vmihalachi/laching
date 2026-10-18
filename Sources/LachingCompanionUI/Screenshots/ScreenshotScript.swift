#if os(macOS)
    import AppKit
    import LachingCompanion
    import LachingCore
    import LachingShell
    import LachingStore
    import SwiftUI

    extension ScreenshotCapture {
        /// What a screenshot run's model is built on, so the user's real settings and the App Store are never touched:
        /// an in-memory settings store, a clock stopped at `now` (a Tuesday at 10:30 by default, inside the default active
        /// hours), an idle time of zero, never busy, a signal hub with no system watchers, silent channels, a store with
        /// fixed prices and a fixed `seed`.
        @MainActor
        public struct SampleEnvironment {
            /// The seed every screenshot run's model is made with (`CompanionAppModel`'s `seed`), the same on every side:
            /// the scheduler's jitter and the picker draw the same, so the next line's time reads the same every run.
            public nonisolated static let sampleSeed: UInt64 = 20_261_013

            public let now: Date
            /// The model's seed: `sampleSeed`.
            public let seed: UInt64 = SampleEnvironment.sampleSeed
            public let store: InMemorySettingsStore
            public let clock: FixedClock
            public let idle: ConstantIdle
            public let busy: NeverBusy
            public let signals: SignalHub
            public let speaker: SilentSpeaker
            public let chime: SilentChime
            public let notifier: SilentNotifier
            /// The sample store; set `owned` to shoot what a buyer sees.
            public let backend: FixedPriceBackend

            /// `prices` by product id, as the App Store would show them ("1,99 €").
            public init(prices: [String: String], now: Date = ScreenshotCapture.sampleNow()) {
                self.now = now
                store = InMemorySettingsStore()
                clock = FixedClock(now: now)
                idle = ConstantIdle()
                busy = NeverBusy()
                signals = SignalHub(idle: idle, busy: busy, audio: nil, screenLock: nil, power: nil, appearance: nil)
                speaker = SilentSpeaker()
                chime = SilentChime()
                notifier = SilentNotifier()
                backend = FixedPriceBackend(prices: prices)
            }

            /// A store over `backend` that keeps what it learns in throwaway defaults.
            public func purchases(catalog: ProductCatalog, prefix: String) -> PurchaseStore {
                PurchaseStore(catalog: catalog, backend: backend, defaults: ScreenshotCapture.throwawayDefaults(prefix: prefix))
            }
        }
    }

    /// A notification that never shows and is always allowed, for screenshot runs and previews.
    @MainActor
    public final class SilentNotifier: CompanionNotifying {
        public var onAction: ((String) -> Void)?

        public init() {}

        public func configure() {}

        public func requestAuthorization(completion: @escaping @MainActor (Bool) -> Void) {
            completion(true)
        }

        public func refreshAuthorization(completion: @escaping @MainActor (NotificationPermission) -> Void) {
            completion(.authorized)
        }

        public func post(line: String) {}
    }

    /// A screenshot run over an app's model: each shot in light, then in dark, saved as `<name>-<light|dark>.png` in
    /// `ScreenshotCapture.outputDirectory`, skipping the ones `ScreenshotCapture.wants` doesn't ask for. The app gives
    /// the shots in order (its windows, views at a size, views at their fitting size, the supporter sequence, its own)
    /// and wraps every view in its window root. Debug only, by the app's choice; quits when done.
    @MainActor
    public struct ScreenshotScript<Window: WindowID & RawRepresentable, Settings: CompanionSettings> where Window.RawValue == String {
        /// One picture (two, with the appearances).
        public enum Shot {
            /// The real window, opened by its id and named by its raw value.
            case window(Window)
            /// A view in a window built like the app's at `size`, for a state the real window can't be told to open in
            /// (a later onboarding page).
            case view(name: String, AnyView, size: CGSize)
            /// A view on its own at its fitting size (the panel, a sheet of poses).
            case fitting(name: String, AnyView)
            /// The Support window bought while open (`thanks`: only the thank-you), then opened again (`supporter`: the
            /// tips); ownership is back to what it was afterwards. A run that already owns `product` (it wears a skin the
            /// product unlocks) can't buy it again, so it shoots only `supporter`.
            case supporterSequence(window: Window, product: String, thanks: String, supporter: String)
            /// Anything else: the app writes the picture to the URL it's given.
            case custom(name: String, (URL) async -> Void)
        }

        public let model: CompanionAppModel<Window, Settings>
        public let backend: FixedPriceBackend
        /// The app's window root around a view.
        public let root: (AnyView) -> AnyView
        /// The windows' background.
        public let paper: NSColor

        public init(
            model: CompanionAppModel<Window, Settings>, backend: FixedPriceBackend, root: @escaping (AnyView) -> AnyView, paper: NSColor
        ) {
            self.model = model
            self.backend = backend
            self.root = root
            self.paper = paper
        }

        /// The whole run: keeps the app regular between windows, empties the output folder, loads the sample prices,
        /// lets the app `prepare` (a line in the bubble), takes `shots` in both appearances and quits
        /// (`ScreenshotCapture.finish()`).
        public func run(prepare: @escaping @MainActor () async -> Void, shots: [Shot]) {
            if ScreenshotCapture.isHidden {
                model.windows.opensHidden = true
            } else {
                model.windows.staysRegular = true  // so the app keeps activation from one shot to the next
            }
            Task {
                ScreenshotCapture.prepareOutputDirectory()
                // launch() is skipped in a screenshot run, so load the sample prices before Support shows them.
                await model.purchases.loadProducts()
                await prepare()
                await take(shots)
                ScreenshotCapture.finish()
            }
        }

        /// Takes `shots` in light, then in dark, into `ScreenshotCapture.outputDirectory`.
        public func take(_ shots: [Shot]) async {
            let directory = ScreenshotCapture.outputDirectory
            for (appearance, name) in [(NSAppearance.Name.aqua, "light"), (.darkAqua, "dark")] {
                NSApplication.shared.appearance = NSAppearance(named: appearance)
                for shot in shots {
                    await take(shot, appearance: name, in: directory)
                }
            }
        }

        private func take(_ shot: Shot, appearance: String, in directory: URL) async {
            func url(_ name: String) -> URL { directory.appendingPathComponent("\(name)-\(appearance).png") }
            switch shot {
            case .window(let id):
                guard ScreenshotCapture.wants(id.rawValue) else { return }
                model.windows.show(id)
                try? await Task.sleep(for: .seconds(1.2))
                if let window = model.windows.window(for: id) {
                    await ScreenshotCapture.capture(window, to: url(id.rawValue))
                } else {
                    ScreenshotCapture.report("\(id.rawValue)-\(appearance).png: the window didn't open")
                }
                model.windows.close(id)
            case .view(let name, let view, let size):
                guard ScreenshotCapture.wants(name) else { return }
                await ScreenshotCapture.captureWindow(root(view), size: size, background: paper, to: url(name))
            case .fitting(let name, let view):
                guard ScreenshotCapture.wants(name) else { return }
                await ScreenshotCapture.captureFitting(root(view), to: url(name))
            case .supporterSequence(let id, let product, let thanks, let supporter):
                let owned = backend.owned
                let names = owned.contains(product) ? [supporter] : [thanks, supporter]
                guard names.contains(where: { ScreenshotCapture.wants($0) }) else { return }
                model.windows.show(id)
                try? await Task.sleep(for: .seconds(0.6))
                backend.owned = owned.union([product])
                await model.purchases.refreshEntitlements()
                try? await Task.sleep(for: .seconds(1.2))
                for name in names {
                    if ScreenshotCapture.wants(name), let window = model.windows.window(for: id) {
                        await ScreenshotCapture.capture(window, to: url(name))
                    }
                    model.windows.close(id)
                    model.windows.show(id)
                    try? await Task.sleep(for: .seconds(1.2))
                }
                model.windows.close(id)
                if backend.owned != owned {
                    backend.owned = owned
                    await model.purchases.refreshEntitlements()
                }
            case .custom(let name, let capture):
                guard ScreenshotCapture.wants(name) else { return }
                await capture(url(name))
            }
        }
    }
#endif
