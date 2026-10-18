import LachingCompanion
import LachingCore
import LachingTesting
import XCTest

@MainActor
final class ChatterCadenceTests: XCTestCase {
    func testPresets() async {
        let minutes = Frequency.allCases.map { [Int($0.interval / 60), Int($0.jitter / 60)] }
        XCTAssertEqual(minutes, [[15, 5], [30, 10], [45, 10], [60, 15], [90, 20], [120, 30], [180, 45]])
        XCTAssertEqual(
            Frequency.allCases.map(\.rawValue),
            ["chatty", "sometimes", "every45Minutes", "rarely", "every90Minutes", "every2Hours", "every3Hours"])
    }

    func testConfigMapsSettingsOntoTheScheduler() async {
        let config = ChatterCadence.config(
            frequency: .chatty, activeStartMinute: 8 * 60, activeEndMinute: 20 * 60, activeWeekdays: [1, 7],
            quietHours: [13], hushInFullScreen: false)
        XCTAssertEqual(config.interval, 900)
        XCTAssertEqual(config.intervalJitter, 300)
        XCTAssertEqual(config.activeStartMinute, 480)
        XCTAssertEqual(config.activeEndMinute, 1200)
        XCTAssertEqual(config.activeWeekdays, [1, 7])
        XCTAssertEqual(config.quietHours, [13])
        XCTAssertEqual(config.idleThreshold, 300)
        XCTAssertTrue(config.postponeDuringCalls)
        XCTAssertFalse(config.postponeDuringFullScreen)
        XCTAssertEqual(config.busyGrace, 30)
    }

    func testDefaultSettingsGiveTheDefaultConfig() async {
        let settings = CompanionSettings(store: InMemorySettingsStore(), defaults: .fixture)
        let config = ChatterCadence.config(for: settings)
        XCTAssertEqual(config.interval, 3600)
        XCTAssertEqual(config.intervalJitter, 900)
        XCTAssertEqual(config.activeWeekdays, [2, 3, 4, 5, 6])
        XCTAssertTrue(config.postponeDuringFullScreen)
    }
}
