import LachingCompanion
import LachingCore
import LachingTesting
import XCTest

@MainActor
final class CompanionCoordinatorTests: XCTestCase {
    private let calendar = ConformanceSupport.utc
    private var clock: MockClock!
    private var idle: MockIdle!
    private var busy: MockBusy!
    private var settings: CompanionSettings!

    private func makeCoordinator(
        start: Date, seed: UInt64 = 42, language: String = "en", configure: (CompanionSettings) -> Void = { _ in }
    ) -> CompanionCoordinator {
        clock = MockClock(start)
        idle = MockIdle()
        busy = MockBusy()
        settings = CompanionSettings(store: InMemorySettingsStore(), defaults: .fixture)
        settings.frequency = .sometimes  // every 30 minutes: these tests count lines on that clock
        configure(settings)
        return CompanionCoordinator(
            settings: settings, bank: .fixture, language: language, clock: clock, idle: idle, busy: busy, calendar: calendar, seed: seed)
    }

    /// Ticks once a minute with the given snapshot, returning the minutes at which a line was delivered.
    private func run(_ c: CompanionCoordinator, minutes: Int, snapshot: Snapshot = .calm) -> [(Int, Delivery)] {
        idle.idle = snapshot.idleSeconds
        busy.reasons = snapshot.busy
        var out: [(Int, Delivery)] = []
        for m in 1...minutes {
            clock.advance(minutes: 1)
            if let delivery = c.tick(snapshot: snapshot) { out.append((m, delivery)) }
        }
        return out
    }

    func testAmbientChatterArrivesAroundTheInterval() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        let deliveries = run(c, minutes: 100)
        XCTAssertEqual(deliveries.count, 3)
        for (minute, delivery) in deliveries {
            XCTAssertEqual(delivery.trigger, .due)
            XCTAssertFalse(delivery.phrase.hasEventTag)
            XCTAssertTrue(delivery.plan.speak)
            XCTAssertTrue((20...40).contains(minute) || (40...80).contains(minute) || (60...120).contains(minute), "\(minute)")
        }
        XCTAssertEqual(c.recentIDs.count, 3)
        XCTAssertEqual(c.lastDelivery, deliveries.last?.1)
    }

    func testPokeSaysSomethingNowAndRestartsTheInterval() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        let delivery = c.poke(snapshot: .calm)
        XCTAssertEqual(delivery?.trigger, .buttonPress)
        XCTAssertTrue(delivery?.phrase.tags.contains(.buttonPress) ?? false)
        XCTAssertFalse(delivery?.plan.notify ?? true)
        XCTAssertEqual(run(c, minutes: 19).count, 0)
    }

    func testMutedAndUnavailableLinesAndFlavorsTurnedOffNeverPlay() {
        let pokes = PhraseBank.fixture.phrases(tagged: .buttonPress).map(\.id)
        let c = makeCoordinator(start: ConformanceSupport.monday(10)) { $0.mutedPhraseIDs = Set(pokes.dropFirst(2)) }
        c.unavailableIDs = [pokes[0]]
        for _ in 0..<4 {
            XCTAssertEqual(c.poke(snapshot: .calm)?.phrase.id, pokes[1])
        }
        settings.mutedPhraseIDs = []
        c.unavailableIDs = []
        settings.phraseFlavors = [FixtureIDs.flavorC]
        let ambient = run(c, minutes: 300).map(\.1.phrase)
        XCTAssertFalse(ambient.isEmpty)
        for phrase in ambient {
            XCTAssertTrue(phrase.flavor == FixtureIDs.flavorC || phrase.isTune, phrase.id)
        }
    }

    func testShushForAnHourThenOneLineOnWaking() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        c.shush(.hour)
        XCTAssertTrue(c.isShushed)
        XCTAssertEqual(settings.shushedUntil, ConformanceSupport.monday(11))
        XCTAssertEqual(c.mood(snapshot: .calm), .shushed)
        XCTAssertEqual(run(c, minutes: 59).count, 0)
        let after = run(c, minutes: 2)
        XCTAssertEqual(after.map(\.1.trigger), [.shushEnd])
        XCTAssertFalse(c.isShushed)
        XCTAssertNil(settings.shushedUntil)
    }

    func testWakeEndsShushEarly() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        c.shush(.untilISay)
        XCTAssertEqual(run(c, minutes: 300).count, 0)
        XCTAssertEqual(c.wake(snapshot: .calm)?.trigger, .shushEnd)
        XCTAssertFalse(c.isShushed)
        XCTAssertNil(c.wake(snapshot: .calm))
    }

    func testShushRestOfTodayIsRestoredFromSettings() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        c.shush(.restOfToday)
        XCTAssertEqual(
            settings.shushedUntil, calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: ConformanceSupport.monday(10))))
        let again = CompanionCoordinator(
            settings: settings, bank: .fixture, language: "en", clock: clock, idle: idle, busy: busy, calendar: calendar, seed: 1)
        XCTAssertTrue(again.isShushed)
    }

    func testACallHoldsTheLineAndReleasesItAsAfterCall() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        _ = run(c, minutes: 15)
        let held = run(c, minutes: 40, snapshot: Snapshot(busy: [.call]))
        XCTAssertEqual(held.count, 0)
        XCTAssertEqual(c.mood(snapshot: Snapshot(busy: [.call])), .hushedForCall)
        let released = run(c, minutes: 3)
        XCTAssertEqual(released.map(\.1.trigger), [.afterCall])
        XCTAssertEqual(released.first?.1.phrase.tags, [.afterCall])
    }

    func testFullScreenHoldsTheLineAndReleasesItAsAPlainLine() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        _ = run(c, minutes: 15)
        let held = run(c, minutes: 40, snapshot: Snapshot(busy: [.fullScreen]))
        XCTAssertEqual(held.count, 0)
        XCTAssertEqual(c.scheduler.status, .postponed(.fullScreen))
        let released = run(c, minutes: 3)
        XCTAssertEqual(released.map(\.1.trigger), [.due])
        XCTAssertEqual(released.first?.1.phrase.hasEventTag, false)
    }

    func testWelcomeBackAfterBeingAwayWithACooldown() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        _ = run(c, minutes: 1)
        XCTAssertEqual(run(c, minutes: 10, snapshot: Snapshot(idleSeconds: 600)).count, 0)
        let back = run(c, minutes: 1)
        XCTAssertEqual(back.map(\.1.trigger), [.welcomeBack])
        XCTAssertEqual(c.lastWelcomeAt, clock.now)
        _ = run(c, minutes: 6, snapshot: Snapshot(idleSeconds: 600))
        XCTAssertEqual(run(c, minutes: 1).count, 0, "within the cooldown")
    }

    func testEventsRespectTheSettingsAndShh() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10)) { $0.commentOnPower = false }
        XCTAssertNil(c.event(.batteryLow, snapshot: .calm))
        XCTAssertEqual(c.event(.wentDark, snapshot: .calm)?.trigger, .wentDark)
        c.shush(.hour)
        XCTAssertNil(c.event(.wentLight, snapshot: .calm))
    }

    func testNothingOutsideActiveHours() {
        let c = makeCoordinator(start: ConformanceSupport.monday(20))
        XCTAssertEqual(run(c, minutes: 120).count, 0)
        XCTAssertNil(c.event(.wentDark, snapshot: .calm))
        XCTAssertEqual(c.mood(snapshot: .calm), .offHours)
        XCTAssertEqual(c.poke(snapshot: .calm)?.trigger, .buttonPress, "the button works at any hour")
    }

    func testSpeakingSkipsAndMutedFallsBack() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10)) { $0.channels = [.voice] }
        XCTAssertNil(c.event(.wentLight, snapshot: Snapshot(isSpeaking: true)))
        // The button answers mid-line (a notification's Say more); the shell cuts the line being said.
        let pressed = c.poke(snapshot: Snapshot(isSpeaking: true))
        XCTAssertEqual(pressed?.trigger, .buttonPress)
        XCTAssertEqual(pressed?.plan, ChannelPlan(speak: true, chime: false, notify: false))
        let muted = c.event(.wentDark, snapshot: Snapshot(isOutputMuted: true, notificationsAuthorized: true))
        XCTAssertEqual(muted?.plan, ChannelPlan(speak: false, chime: false, notify: true))
    }

    func testSettingsChangedRescheduls() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10))
        settings.frequency = .every2Hours
        c.settingsChanged()
        XCTAssertEqual(c.scheduler.config.interval, 7200)
        XCTAssertEqual(run(c, minutes: 89).count, 0)
    }

    func testFullScreenCountsOnlyWhenTheSettingSays() {
        let fullScreen = Snapshot(busy: [.fullScreen])
        let hushed = makeCoordinator(start: ConformanceSupport.monday(10))
        XCTAssertEqual(run(hushed, minutes: 60, snapshot: fullScreen).count, 0)
        XCTAssertEqual(hushed.mood(snapshot: fullScreen), .hushedFullScreen)
        let chatty = makeCoordinator(start: ConformanceSupport.monday(10)) { $0.hushInFullScreen = false }
        XCTAssertGreaterThan(run(chatty, minutes: 60, snapshot: fullScreen).count, 0)
        XCTAssertEqual(chatty.mood(snapshot: fullScreen), .awake)
        XCTAssertEqual(chatty.mood(snapshot: Snapshot(busy: [.call, .fullScreen])), .hushedForCall)
    }

    func testSameSeedSameDay() {
        let a = makeCoordinator(start: ConformanceSupport.monday(10)).poke(snapshot: .calm)
        let b = makeCoordinator(start: ConformanceSupport.monday(10)).poke(snapshot: .calm)
        XCTAssertEqual(a, b)
    }

    func testTheLanguageScopesThePicks() {
        let english = makeCoordinator(start: ConformanceSupport.monday(10)) { $0.channels = .quiet }
        let said = Set(run(english, minutes: 600).map(\.1.phrase.id))
        XCTAssertFalse(said.isEmpty)
        XCTAssertTrue(said.isDisjoint(with: ["it-only", "joke-it"]))

        let italian = makeCoordinator(start: ConformanceSupport.monday(10), language: "it") { $0.channels = .quiet }
        let saidInItalian = Set(run(italian, minutes: 600).map(\.1.phrase.id))
        XCTAssertFalse(saidInItalian.isDisjoint(with: ["it-only", "joke-it"]))
        italian.language = "en"
        for _ in 0..<20 {
            XCTAssertNotEqual(italian.poke(snapshot: .calm)?.phrase.id, "joke-it")
        }
    }

    func testTheSkinScopesThePicks() {
        let c = makeCoordinator(start: ConformanceSupport.monday(10)) { $0.channels = .quiet }
        XCTAssertFalse(run(c, minutes: 600).contains { $0.1.phrase.id == "skin-raster-only" })
        let raster = makeCoordinator(start: ConformanceSupport.monday(10)) {
            $0.channels = .quiet
            $0.skin = FixtureIDs.rasterSkin
        }
        XCTAssertTrue(run(raster, minutes: 600).contains { $0.1.phrase.id == "skin-raster-only" })
    }
}
