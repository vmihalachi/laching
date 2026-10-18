#if os(macOS) || os(iOS)
    import LachingCompanion
    import LachingCore
    import LachingStore
    import SwiftUI
    #if os(macOS)
        import AppKit
        import LachingShell
    #elseif os(iOS)
        import UIKit
    #endif

    /// Renders an app's windows and views to PNGs in-process (no Screen Recording permission), for documentation and
    /// store screenshots: the generic half of a screenshot mode. The app's run script sets `LACHING_SCREENSHOT_MODE` (a
    /// comma-separated list of shot name prefixes, or `all`) and optionally `LACHING_SCREENSHOT_DIR` (else a folder in
    /// the temporary directory, which a sandboxed app's script copies out of its container: `finish()` prints its path
    /// last). The app builds its model over a throwaway defaults suite, a fixed clock and the silent fakes below, so the
    /// user's settings, speakers and the App Store are never touched, then shoots what it wants. Needs the main actor.
    ///
    /// Every shot is also checked: a window that couldn't be drawn, a PNG that wasn't written, or SwiftUI content taller
    /// than its window (`LayoutCheck`) is a problem. `finish()` writes them to `problems.txt` in the output folder, which
    /// `tools/screenshots.sh` prints and fails on; `report(_:)` adds the app's own.
    ///
    /// On iOS the same run draws the key window of a Simulator app once it has settled, sheets and covers included,
    /// and checks it the same way (`capture(to:)` in `ScreenshotCapture+iOS.swift`); `tools/check-layout-ios.py` runs it.
    @MainActor
    public enum ScreenshotCapture {
        public static let modeVariable = "LACHING_SCREENSHOT_MODE"
        public static let directoryVariable = "LACHING_SCREENSHOT_DIR"

        /// `1`: the run opens its windows hidden (`WindowManager.opensHidden`) and doesn't wait for them to be key, so it
        /// leaves the Mac to whoever is using it; the shots show the windows inactive. The scripts set it for `--only`
        /// and the layout check.
        public static let hiddenVariable = "LACHING_SCREENSHOT_HIDDEN"

        /// The skin the run wears (a `SkinID` raw value: `auguste`), so each costume gets its own set of shots; unset, the
        /// app's default skin. The app wears it before its shots, owning in the sample store whatever unlocks it.
        /// `tools/screenshots.sh --skin` sets it.
        public static let skinVariable = "LACHING_SCREENSHOT_SKIN"

        public static var isEnabled: Bool { ProcessInfo.processInfo.environment[modeVariable] != nil }

        public static var isHidden: Bool { ProcessInfo.processInfo.environment[hiddenVariable] == "1" }

        /// The skin asked for (`skinVariable`), else nil.
        public static var skin: SkinID? {
            guard isEnabled, let raw = ProcessInfo.processInfo.environment[skinVariable], !raw.isEmpty else { return nil }
            return SkinID(rawValue: raw)
        }

        /// The shot name prefixes asked for; empty means all.
        public static var wanted: [String] {
            (ProcessInfo.processInfo.environment[modeVariable] ?? "")
                .split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty && $0 != "all" }
        }

        /// Whether `shot` was asked for (by a prefix of its name).
        public static func wants(_ shot: String) -> Bool {
            let wanted = wanted
            return wanted.isEmpty || wanted.contains { shot.hasPrefix($0) }
        }

        public static var outputDirectory: URL {
            if let dir = ProcessInfo.processInfo.environment[directoryVariable], !dir.isEmpty {
                return URL(fileURLWithPath: dir, isDirectory: true)
            }
            // One per process, so runs in several languages at once (the layout check) don't share it.
            return FileManager.default.temporaryDirectory.appendingPathComponent(
                "screenshots-\(ProcessInfo.processInfo.processIdentifier)", isDirectory: true)
        }

        /// The file `finish()` writes the run's problems to, next to the shots; absent when there were none.
        public static let problemsFile = "problems.txt"

        /// What the run found wrong so far, one line each, starting with the shot's file name.
        public private(set) static var problems: [String] = []

        /// Records a problem (once), for example a window the app expected to open and didn't, and prints it to
        /// standard error as it happens.
        public static func report(_ problem: String) {
            guard !problems.contains(problem) else { return }
            problems.append(problem)
            FileHandle.standardError.write(Data("\(problem)\n".utf8))
        }

        /// Empties and creates `outputDirectory`.
        public static func prepareOutputDirectory() {
            try? FileManager.default.removeItem(at: outputDirectory)
            try? FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
        }

        /// A `UserDefaults` suite of its own, so nothing a shot changes reaches the user's real defaults.
        public static func throwawayDefaults(prefix: String) -> UserDefaults {
            UserDefaults(suiteName: "\(prefix).screenshots.\(UUID().uuidString)")!
        }

        /// The next moment at `hour`:`minute` local time on `weekday` (1 = Sunday … 7 = Saturday), such as a Tuesday at
        /// 10:30, inside the default active hours.
        public static func sampleNow(hour: Int = 10, minute: Int = 30, weekday: Int = 3, calendar: Calendar = .current) -> Date {
            calendar.nextDate(
                after: Date(), matching: DateComponents(hour: hour, minute: minute, weekday: weekday), matchingPolicy: .nextTime)
                ?? Date()
        }

        /// Writes the problems found (`problemsFile`), prints the output folder (the run script reads the last line) and
        /// quits.
        public static func finish() {
            if !problems.isEmpty {
                try? problems.joined(separator: "\n").appending("\n").write(
                    to: outputDirectory.appendingPathComponent(problemsFile), atomically: true, encoding: .utf8)
            }
            print(outputDirectory.path)
            #if os(macOS)
                NSApp.terminate(nil)
            #else
                exit(0)  // an iOS app can't quit itself otherwise; `simctl launch --console` returns when it ends
            #endif
        }
    }

    /// A clock stopped at one moment.
    public struct FixedClock: Clock, Sendable {
        public let now: Date

        public init(now: Date) {
            self.now = now
        }
    }

    /// Never idle: the user is always there.
    public struct ConstantIdle: IdleTimeProvider, Sendable {
        public init() {}

        public func secondsSinceLastInput() -> TimeInterval { 0 }
    }

    /// Fixed prices (such as the app's `Products.storekit`), without asking the App Store. Purchases are cancelled;
    /// `owned` is what the user is entitled to, set by the shot.
    @MainActor
    public final class FixedPriceBackend: StoreBackend {
        public let prices: [String: String]
        public var owned: Set<String> = []

        /// `prices` maps product ids to display prices ("1,99 €").
        public init(prices: [String: String]) {
            self.prices = prices
        }

        public func loadProducts(_ ids: [String]) async throws -> [StoreProduct] {
            ids.compactMap { id in prices[id].map { StoreProduct(id: id, displayPrice: $0) } }
        }

        public func entitledProductIDs() async -> Set<String> { owned }

        public func purchase(_ id: String) async throws -> PurchaseOutcome { .cancelled }

        public func restore() async throws {}

        public func observeTransactions(_ onUpdate: @escaping @MainActor (TransactionUpdate) -> Void) {}
    }
#endif
