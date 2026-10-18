import LachingCompanion
import LachingCore
import LachingTesting
import Observation
import XCTest

/// The subclass from `CompanionSettings`' doc comment, as an app writes it in its own module.
@Observable
final class AppSettings: CompanionSettings {
    var voice: String? {
        didSet {
            if let voice { store.set(string: voice, forKey: "voice") } else { store.remove(forKey: "voice") }
        }
    }

    override init(store: any SettingsStore, defaults: CompanionDefaults) {
        voice = store.string(forKey: "voice")
        super.init(store: store, defaults: defaults)
    }

    override func resetToDefaults() {
        super.resetToDefaults()
        voice = nil
    }
}

private final class Count: @unchecked Sendable {
    var value = 0
}

@MainActor
final class CompanionSettingsSubclassTests: XCTestCase {
    func testAnAppsOwnKeyWritesThroughAndResets() async {
        let store = InMemorySettingsStore()
        let settings = AppSettings(store: store, defaults: .fixture)
        XCTAssertNil(settings.voice)
        settings.voice = "female"
        settings.frequency = .chatty
        XCTAssertEqual(store.string(forKey: "voice"), "female")
        XCTAssertEqual(AppSettings(store: store, defaults: .fixture).voice, "female")
        settings.resetToDefaults()
        XCTAssertNil(settings.voice)
        XCTAssertEqual(settings.frequency, .rarely)
        XCTAssertNil(store.string(forKey: "voice"))
        XCTAssertTrue(store.keys.isEmpty)
    }

    func testBothTheBasesAndTheSubclasssPropertiesNotify() async {
        let settings = AppSettings(store: InMemorySettingsStore(), defaults: .fixture)
        let base = Count()
        withObservationTracking {
            _ = settings.manners
        } onChange: {
            base.value += 1
        }
        settings.commentOnPower = false
        XCTAssertEqual(base.value, 1)

        let own = Count()
        withObservationTracking {
            _ = settings.voice
        } onChange: {
            own.value += 1
        }
        settings.frequency = .every3Hours
        XCTAssertEqual(own.value, 0)
        settings.voice = "male"
        XCTAssertEqual(own.value, 1)
    }

    func testTheCoordinatorTakesTheSubclass() async {
        let settings = AppSettings(store: InMemorySettingsStore(), defaults: .fixture)
        let coordinator = CompanionCoordinator(
            settings: settings, bank: .fixture, language: "en", clock: MockClock(Date(timeIntervalSince1970: 0)), idle: MockIdle(),
            busy: MockBusy(), calendar: Calendar(identifier: .gregorian), seed: 1)
        XCTAssertTrue(coordinator.settings === settings)
    }
}
