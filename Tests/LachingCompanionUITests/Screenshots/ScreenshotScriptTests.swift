#if os(macOS)
    import AppKit
    import LachingCompanion
    import LachingCompanionUI
    import LachingCore
    import LachingShell
    import LachingStore
    import LachingTesting
    import SwiftUI
    import XCTest

    @MainActor
    final class ScreenshotScriptTests: XCTestCase {
        func testAScriptWritesEveryShotInBothAppearances() async throws {
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("laching-shots-\(UUID().uuidString)")
            setenv(ScreenshotCapture.directoryVariable, directory.path, 1)
            defer { unsetenv(ScreenshotCapture.directoryVariable) }
            ScreenshotCapture.prepareOutputDirectory()

            let sample = ScreenshotCapture.SampleEnvironment(prices: ["fixture.supporter": "1,99 €"])
            let settings = CompanionSettings(store: sample.store, defaults: .fixture)
            let model = FixtureModel(
                settings: settings, bank: .fixture,
                roles: WindowRoles(home: .home, onboarding: .onboarding, settings: .settings, support: .support),
                configuration: FixtureModel.configuration, clock: sample.clock, idle: sample.idle, busy: sample.busy,
                calendar: .current, speaker: sample.speaker, chime: sample.chime, notifier: sample.notifier, signals: sample.signals,
                purchases: sample.purchases(catalog: ProductCatalog(unlockables: ["fixture.supporter"]), prefix: "laching.tests"),
                voices: VoiceLibrary(folder: nil, language: "en", allowTesting: true), seed: sample.seed)
            var custom: [String] = []
            let script = ScreenshotScript(
                model: model, backend: sample.backend,
                root: { AnyView($0.companionWindowRoot(palette: .system, displayFont: { .system(size: $0) })) },
                paper: .white)
            await script.take([
                .fitting(name: "panel", AnyView(Text(verbatim: "Panel").padding())),
                .custom(name: "sheet") { url in custom.append(url.lastPathComponent) },
            ])

            let files = try FileManager.default.contentsOfDirectory(atPath: directory.path).sorted()
            XCTAssertEqual(files, ["panel-dark.png", "panel-light.png"])
            XCTAssertEqual(custom, ["sheet-light.png", "sheet-dark.png"])
            XCTAssertTrue(ScreenshotCapture.problems.isEmpty, "\(ScreenshotCapture.problems)")
            try? FileManager.default.removeItem(at: directory)
        }

        func testTheThemesAskedForAreShotInOrderAndBothWhenUnset() {
            XCTAssertEqual(ScreenshotCapture.themes(nil), ["light", "dark"])
            XCTAssertEqual(ScreenshotCapture.themes(""), ["light", "dark"])
            XCTAssertEqual(ScreenshotCapture.themes("dark"), ["dark"])
            XCTAssertEqual(ScreenshotCapture.themes("dark, light"), ["dark", "light"])
            XCTAssertEqual(ScreenshotCapture.themes("contrast"), ["light", "dark"])
            XCTAssertEqual(ScreenshotCapture.appearances.map(\.appearance), [.aqua, .darkAqua])
        }

        func testTheSeedIsTheSameEveryRunAndOnEverySide() {
            let first = ScreenshotCapture.SampleEnvironment(prices: [:])
            let second = ScreenshotCapture.SampleEnvironment(prices: [:])
            XCTAssertEqual(first.seed, second.seed)
            XCTAssertEqual(first.seed, 20_261_013)  // C#'s SampleEnvironment.SampleSeed
        }
    }
#endif
