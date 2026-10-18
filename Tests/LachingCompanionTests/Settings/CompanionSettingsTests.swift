import LachingCompanion
import LachingCore
import LachingTesting
import XCTest

@MainActor
final class CompanionSettingsTests: XCTestCase {
    func testDefaultsOnAFreshStore() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertEqual(settings.channels, [.voice])
        XCTAssertEqual(settings.frequency, .rarely)
        XCTAssertEqual(settings.activeStartMinute, 540)
        XCTAssertEqual(settings.activeEndMinute, 1080)
        XCTAssertEqual(settings.activeWeekdays, [2, 3, 4, 5, 6])
        XCTAssertEqual(settings.quietHours, [])
        XCTAssertTrue(settings.hushInFullScreen)
        XCTAssertTrue(settings.greetOnReturn)
        XCTAssertNil(settings.voiceIdentifier)
        XCTAssertEqual(settings.speechRate, 0.5)
        XCTAssertEqual(settings.appearance, .system)
        XCTAssertEqual(settings.skin, FixtureIDs.vectorSkin)
        XCTAssertNil(settings.shushedUntil)
        XCTAssertFalse(settings.hasCompletedOnboarding)
        // Reading defaults writes nothing.
        XCTAssertTrue(store.keys.isEmpty)
    }

    func testChangesWriteThroughUnderStableKeys() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        settings.channels = [.chime, .notification]
        settings.frequency = .chatty
        settings.activeWeekdays = [7, 1]
        settings.quietHours = [13]
        settings.voiceIdentifier = "com.apple.voice.compact.en-US.Samantha"
        settings.shushedUntil = Date(timeIntervalSince1970: 1_800_000_000)
        settings.hasCompletedOnboarding = true
        XCTAssertEqual(store.strings(forKey: SettingsKeys.channels), ["chime", "notification"])
        XCTAssertEqual(store.string(forKey: SettingsKeys.frequency), "chatty")
        XCTAssertEqual(store.integers(forKey: SettingsKeys.activeWeekdays), [1, 7])
        XCTAssertEqual(store.integers(forKey: SettingsKeys.quietHours), [13])
        XCTAssertEqual(store.string(forKey: SettingsKeys.voiceIdentifier), "com.apple.voice.compact.en-US.Samantha")
        XCTAssertEqual(store.double(forKey: SettingsKeys.shushedUntil), 1_800_000_000)
        XCTAssertEqual(store.bool(forKey: SettingsKeys.hasCompletedOnboarding), true)

        let reloaded = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertEqual(reloaded.channels, [.chime, .notification])
        XCTAssertEqual(reloaded.frequency, .chatty)
        XCTAssertEqual(reloaded.activeWeekdays, [1, 7])
        XCTAssertEqual(reloaded.shushedUntil, Date(timeIntervalSince1970: 1_800_000_000))
        XCTAssertTrue(reloaded.hasCompletedOnboarding)
    }

    func testFlavorsAndMutedLinesRoundTripAndReset() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertEqual(settings.phraseFlavors, FixtureIDs.flavors)
        XCTAssertEqual(settings.mutedPhraseIDs, [])
        settings.phraseFlavors = [FixtureIDs.flavorC, FixtureIDs.flavorB]
        settings.mutedPhraseIDs = ["general-a2"]
        XCTAssertEqual(store.strings(forKey: SettingsKeys.phraseFlavors), ["b", "c"])
        XCTAssertEqual(store.strings(forKey: SettingsKeys.mutedPhraseIDs), ["general-a2"])
        let reloaded = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertEqual(reloaded.phraseFlavors, [FixtureIDs.flavorC, FixtureIDs.flavorB])
        XCTAssertEqual(reloaded.mutedPhraseIDs, ["general-a2"])
        reloaded.resetToDefaults()
        XCTAssertEqual(reloaded.phraseFlavors, FixtureIDs.flavors)
        XCTAssertEqual(reloaded.mutedPhraseIDs, [])
    }

    func testTheRetiredShortcutKeyIsIgnored() {
        // 0.8 stored the global shortcut under "sayShortcut"; a value left from then reads as nothing and stays put.
        let store = InMemorySettingsStore()
        store.set(string: #"{"keyCode":3,"modifiers":6400,"key":"F"}"#, forKey: "sayShortcut")
        let settings = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertFalse(SettingsKeys.all.contains("sayShortcut"))
        XCTAssertEqual(settings.defaults, .fixture)
    }

    func testUnknownStoredValuesFallBackToDefaults() {
        let store = InMemorySettingsStore()
        store.set(string: "sometimes-ish", forKey: SettingsKeys.frequency)
        store.set(strings: ["voice", "drums"], forKey: SettingsKeys.channels)
        store.set(string: "", forKey: SettingsKeys.skin)
        store.set(integer: 3, forKey: SettingsKeys.appearance)
        let settings = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertEqual(settings.frequency, .rarely)
        XCTAssertEqual(settings.channels, [.voice])
        XCTAssertEqual(settings.skin, FixtureIDs.vectorSkin)
        XCTAssertEqual(settings.appearance, .system)
    }

    func testSkinsAndFlavorsAreTheAppsIDs() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        settings.skin = FixtureIDs.rasterSkin
        XCTAssertEqual(store.string(forKey: SettingsKeys.skin), "fixture-raster")
        // Validity is the app's: an id the package doesn't know is kept as stored.
        store.set(string: "retired-skin", forKey: SettingsKeys.skin)
        store.set(strings: ["a", "retired"], forKey: SettingsKeys.phraseFlavors)
        let reloaded = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertEqual(reloaded.skin, "retired-skin")
        XCTAssertEqual(reloaded.phraseFlavors, ["a", "retired"])
    }

    func testDefaultsComeFromTheApp() {
        var defaults = CompanionDefaults(skin: "one", phraseFlavors: ["x"])
        defaults.frequency = .chatty
        defaults.channels = [.voice, .chime]
        let settings = CompanionSettings(store: InMemorySettingsStore(), defaults: defaults)
        XCTAssertEqual(settings.skin, "one")
        XCTAssertEqual(settings.phraseFlavors, ["x"])
        XCTAssertEqual(settings.frequency, .chatty)
        XCTAssertEqual(settings.channels, [.voice, .chime])
        XCTAssertEqual(settings.defaults, defaults)
    }

    func testResetKeepsOnboardingDone() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        settings.frequency = .every2Hours
        settings.hasCompletedOnboarding = true
        settings.resetToDefaults()
        XCTAssertEqual(settings.frequency, .rarely)
        XCTAssertTrue(settings.hasCompletedOnboarding)
        XCTAssertEqual(SettingsKeys.all.count, 22)
        XCTAssertEqual(Set(SettingsKeys.all).count, 22)
        XCTAssertEqual(SettingsKeys.resettable.count, SettingsKeys.all.count - 1)
        XCTAssertFalse(SettingsKeys.resettable.contains(SettingsKeys.hasCompletedOnboarding))
    }

    func testResetRemovesTheStoredValuesSoLaterDefaultsApply() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        settings.frequency = .every2Hours
        settings.speechRate = 0.9
        settings.activeWeekdays = [1, 7]
        settings.hasCompletedOnboarding = true
        settings.resetToDefaults()
        for key in SettingsKeys.resettable {
            XCTAssertFalse(store.keys.contains(key), key)
        }
        XCTAssertTrue(store.keys.contains(SettingsKeys.hasCompletedOnboarding))

        var newDefaults = CompanionDefaults.fixture
        newDefaults.frequency = .chatty
        newDefaults.speechRate = 0.3
        newDefaults.activeWeekdays = [2, 3]
        let reloaded = CompanionSettings(store: store, defaults: newDefaults)
        XCTAssertEqual(reloaded.frequency, .chatty)
        XCTAssertEqual(reloaded.speechRate, 0.3)
        XCTAssertEqual(reloaded.activeWeekdays, [2, 3])
        XCTAssertTrue(reloaded.hasCompletedOnboarding)
    }

    func testDesktopSettingsRoundTripAndReset() {
        let store = InMemorySettingsStore()
        let settings = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertFalse(settings.showOnDesktop)
        XCTAssertEqual(settings.desktopSize, .medium)
        XCTAssertNil(settings.desktopPosition)
        settings.showOnDesktop = true
        settings.desktopSize = .large
        settings.desktopPosition = DesktopPosition(x: 1300, y: -40)
        XCTAssertEqual(store.bool(forKey: SettingsKeys.showOnDesktop), true)
        XCTAssertEqual(store.string(forKey: SettingsKeys.desktopSize), "large")
        XCTAssertEqual(store.integers(forKey: SettingsKeys.desktopPosition), [1300, -40])

        let reloaded = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertTrue(reloaded.showOnDesktop)
        XCTAssertEqual(reloaded.desktopSize, .large)
        XCTAssertEqual(reloaded.desktopPosition, DesktopPosition(x: 1300, y: -40))

        reloaded.resetToDefaults()
        XCTAssertFalse(reloaded.showOnDesktop)
        XCTAssertEqual(reloaded.desktopSize, .medium)
        XCTAssertNil(reloaded.desktopPosition)
        XCTAssertNil(store.integers(forKey: SettingsKeys.desktopPosition))
    }

    func testAnUnreadableDesktopPositionMeansTheCorner() {
        let store = InMemorySettingsStore()
        store.set(integers: [12], forKey: SettingsKeys.desktopPosition)
        store.set(string: "huge", forKey: SettingsKeys.desktopSize)
        let settings = CompanionSettings(store: store, defaults: .fixture)
        XCTAssertNil(settings.desktopPosition)
        XCTAssertEqual(settings.desktopSize, .medium)
    }

    func testMannersMirrorTheSettings() {
        let settings = CompanionSettings(store: InMemorySettingsStore(), defaults: .fixture)
        settings.commentOnPower = false
        XCTAssertEqual(settings.manners, Manners(channels: [.voice], greetOnReturn: true, commentOnPower: false, commentOnAppearance: true))
    }
}
