import LachingCore
import LachingTesting
import XCTest

@MainActor
final class ReminderSchedulerTests: XCTestCase {
    private let calendar = ConformanceSupport.utc

    private var clock: MockClock!
    private var idle: MockIdle!
    private var busy: MockBusy!

    private func makeScheduler(start: Date, config: SchedulerConfig = SchedulerConfig()) -> ReminderScheduler {
        clock = MockClock(start)
        idle = MockIdle()
        busy = MockBusy()
        return ReminderScheduler(config: config, clock: clock, idle: idle, busy: busy, calendar: calendar)
    }

    /// Advances in 20 s ticks (like the app), returning the ticks' decisions.
    private func tick(_ s: ReminderScheduler, seconds: Int) -> [SchedulerDecision] {
        (0..<(seconds / 20)).map { _ in
            clock.now = clock.now.addingTimeInterval(20)
            return s.tick()
        }
    }

    /// Advances minute by minute, returning the minutes (from now) at which reminders fired.
    private func run(_ s: ReminderScheduler, minutes: Int) -> [Int] {
        var fired: [Int] = []
        for m in 1...minutes {
            clock.advance(minutes: 1)
            if s.tick() == .remind { fired.append(m) }
        }
        return fired
    }

    func testMondayFixtureIsAWeekday() async {
        XCTAssertEqual(calendar.component(.weekday, from: ConformanceSupport.monday(10)), 2)
    }

    func testRemindsAfterInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(9, 30))
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 15))
        XCTAssertEqual(run(s, minutes: 100), [45, 90])
    }

    func testCustomInterval() async {
        var config = SchedulerConfig()
        config.interval = 20 * 60
        let s = makeScheduler(start: ConformanceSupport.monday(10), config: config)
        XCTAssertEqual(run(s, minutes: 60), [20, 40, 60])
    }

    func testIdleUserIsNotRemindedAndTimerResets() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 30)
        idle.idle = 10 * 60  // user walked away
        XCTAssertEqual(run(s, minutes: 30), [])
        XCTAssertEqual(s.status, .away)
        idle.idle = 0  // back at the desk: a fresh full interval starts
        XCTAssertEqual(run(s, minutes: 50), [45])
    }

    func testShortIdleDoesNotCountAsAway() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        idle.idle = 2 * 60
        XCTAssertEqual(run(s, minutes: 45), [45])
    }

    func testNoRemindersOutsideActiveHours() async {
        let s = makeScheduler(start: ConformanceSupport.monday(17, 30))
        XCTAssertEqual(run(s, minutes: 29), [])
        clock.advance(minutes: 1)
        XCTAssertEqual(s.tick(), .none)  // 18:00 – window closed
        XCTAssertEqual(s.status, .outsideActiveHours)
        XCTAssertNil(s.nextFireDate)
        XCTAssertEqual(run(s, minutes: 300), [])
    }

    func testEnteringActiveHoursStartsFreshInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(8, 0))
        XCTAssertNil(s.nextFireDate)
        // 9:00 enters the window; first reminder at 9:45.
        XCTAssertEqual(run(s, minutes: 110), [105])
    }

    func testNoRemindersOnWeekend() async {
        let saturday = calendar.date(from: DateComponents(year: 2026, month: 9, day: 26, hour: 10))!
        XCTAssertEqual(calendar.component(.weekday, from: saturday), 7)
        let s = makeScheduler(start: saturday)
        XCTAssertEqual(run(s, minutes: 200), [])
    }

    func testOvernightActiveWindow() async {
        var config = SchedulerConfig()
        config.activeStartMinute = 22 * 60
        config.activeEndMinute = 6 * 60
        config.activeWeekdays = Set(1...7)
        let s = makeScheduler(start: ConformanceSupport.monday(12), config: config)
        XCTAssertFalse(s.isWithinActiveHours(ConformanceSupport.monday(12)))
        XCTAssertTrue(s.isWithinActiveHours(ConformanceSupport.monday(23)))
        XCTAssertTrue(s.isWithinActiveHours(ConformanceSupport.monday(2)))
    }

    func testSnoozeDelaysByTenMinutes() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        XCTAssertEqual(run(s, minutes: 45), [45])
        s.snooze(minutes: 10)
        XCTAssertEqual(run(s, minutes: 15), [10])
    }

    func testSnoozeForSecondsRemindsAfterThatLong() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        XCTAssertEqual(run(s, minutes: 45), [45])
        s.snooze(for: 90)
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 46).addingTimeInterval(30))
        XCTAssertEqual(tick(s, seconds: 120), [.none, .none, .none, .none, .remind, .none])
    }

    func testSnoozeMinutesIsSnoozeForSixtyTimesAsManySeconds() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 45)
        s.snooze(minutes: 10)
        let byMinutes = s.nextFireDate
        let t = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(t, minutes: 45)
        t.snooze(for: 600)
        XCTAssertEqual(t.nextFireDate, byMinutes)
    }

    func testSnoozeWithoutReminderNeverBringsItCloser() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        XCTAssertEqual(run(s, minutes: 20), [])
        s.snooze(minutes: 10)
        // Still due at 10:45, not 10:30.
        XCTAssertEqual(run(s, minutes: 30), [25])
    }

    func testSnoozeAfterAnsweredReminderNeverBringsItCloser() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        XCTAssertEqual(run(s, minutes: 45), [45])
        s.sessionCompleted()
        s.snooze(minutes: 10)
        XCTAssertEqual(run(s, minutes: 45), [45])
    }

    func testSnoozeHeldReminderRemindsInTenMinutes() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 50), [])
        s.snooze(minutes: 10)
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 10), [10])
    }

    func testSnoozeLaterThanNextReminderPushesItOut() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        XCTAssertEqual(run(s, minutes: 40), [])
        s.snooze(minutes: 10)
        XCTAssertEqual(run(s, minutes: 10), [10])
    }

    func testPauseSuppressesRemindersThenRestartsInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.pause(for: 3600)
        if case .paused = s.status {} else { XCTFail("expected paused") }
        XCTAssertEqual(run(s, minutes: 60), [])
        // Pause expired at +60; next reminder 45 min after that.
        XCTAssertEqual(run(s, minutes: 50), [45])
    }

    func testResumeEndsPauseEarly() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.pause(for: 3600)
        _ = run(s, minutes: 10)
        s.resume()
        XCTAssertEqual(run(s, minutes: 45), [45])
    }

    func testPauseUntilDateLastsAcrossDaysThenRestartsInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        let wednesday = calendar.date(byAdding: .day, value: 2, to: calendar.startOfDay(for: ConformanceSupport.monday(10)))!
        s.pause(until: wednesday)
        XCTAssertEqual(s.status, .paused(until: wednesday))
        clock.now = calendar.date(byAdding: .hour, value: 30, to: ConformanceSupport.monday(10))!
        XCTAssertEqual(s.tick(), .none)
        clock.now = calendar.date(bySettingHour: 10, minute: 0, second: 0, of: wednesday)!
        XCTAssertEqual(s.tick(), .none)
        XCTAssertNil(s.pausedUntil)
        XCTAssertEqual(run(s, minutes: 50), [45])
    }

    func testEndOfTodayIsNextMidnight() async {
        let s = makeScheduler(start: ConformanceSupport.monday(23, 30))
        XCTAssertEqual(s.endOfToday, calendar.date(byAdding: .minute, value: 30, to: ConformanceSupport.monday(23, 30)))
        s.pause(until: s.endOfToday)
        clock.now = ConformanceSupport.monday(23, 59)
        XCTAssertEqual(s.tick(), .none)
        if case .paused = s.status {} else { XCTFail("expected paused") }
    }

    func testSessionCompletedResetsInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 40)
        s.sessionCompleted()
        XCTAssertEqual(run(s, minutes: 50), [45])
    }

    func testChangingIntervalReschedules() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 10)
        var config = SchedulerConfig()
        config.interval = 15 * 60
        s.update(config: config)
        XCTAssertEqual(run(s, minutes: 16), [15])
    }

    // MARK: Busy

    func testCallPostponesDueReminderAndFiresAfterGrace() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 40)
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 20), [])  // due at 45, held
        XCTAssertEqual(s.status, .postponed(.call))
        XCTAssertEqual(s.tick(), .postpone)
        busy.reasons = []
        // Last busy tick at t; +20 s is inside the 30 s grace, +40 s fires.
        XCTAssertEqual(tick(s, seconds: 40), [.postpone, .remind])
        if case .scheduled = s.status {} else { XCTFail("expected scheduled") }
        // Then a full interval from the release.
        XCTAssertEqual(run(s, minutes: 46), [45])
    }

    func testBusyBeforeDueDoesNotChangeTheSchedule() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        busy.reasons = [.fullScreen]
        XCTAssertEqual(run(s, minutes: 30), [])
        if case .scheduled = s.status {} else { XCTFail("expected scheduled") }
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 15), [15])
    }

    func testReturnsPostponeOnlyWhenAReminderIsHeld() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        busy.reasons = [.call]
        clock.advance(minutes: 10)
        XCTAssertEqual(s.tick(), .none)
        clock.advance(minutes: 40)
        XCTAssertEqual(s.tick(), .postpone)
    }

    func testDisabledReasonsAreIgnored() async {
        var config = SchedulerConfig()
        config.postponeDuringFullScreen = false
        let s = makeScheduler(start: ConformanceSupport.monday(10), config: config)
        busy.reasons = [.fullScreen]
        XCTAssertEqual(run(s, minutes: 45), [45])
        config.postponeDuringCalls = false
        s.update(config: config)
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 45), [45])
    }

    func testCallStatusWinsOverFullScreen() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        busy.reasons = [.call, .fullScreen]
        _ = run(s, minutes: 46)
        XCTAssertEqual(s.status, .postponed(.call))
    }

    func testIdleDuringCallIsNotABreak() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 30)
        busy.reasons = [.call]
        idle.idle = 20 * 60  // listening, hands off the keyboard
        XCTAssertEqual(run(s, minutes: 30), [])
        XCTAssertNotEqual(s.status, .away)
        busy.reasons = []
        idle.idle = 0
        XCTAssertEqual(run(s, minutes: 1), [1])  // held reminder, not a reset
    }

    func testWalkingAwayAfterCallDropsHeldReminder() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        busy.reasons = [.call]
        _ = run(s, minutes: 50)
        busy.reasons = []
        idle.idle = 10 * 60
        XCTAssertEqual(run(s, minutes: 5), [])
        XCTAssertEqual(s.status, .away)
        idle.idle = 0
        XCTAssertEqual(run(s, minutes: 46), [45])
    }

    func testHeldReminderIsDroppedWhenActiveHoursEnd() async {
        let s = makeScheduler(start: ConformanceSupport.monday(17))
        busy.reasons = [.call]
        _ = run(s, minutes: 65)  // due 17:45, held; window closes 18:00
        XCTAssertEqual(s.status, .outsideActiveHours)
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 120), [])
        XCTAssertFalse(s.isHolding)
    }

    func testBusyOutsideActiveHoursDoesNothing() async {
        let s = makeScheduler(start: ConformanceSupport.monday(19))
        busy.reasons = [.call]
        XCTAssertEqual(s.tick(), .none)
        XCTAssertEqual(s.status, .outsideActiveHours)
    }

    func testPauseWinsOverBusy() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        busy.reasons = [.call]
        _ = run(s, minutes: 50)
        s.pause(for: 3600)
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 59), [])
        if case .paused = s.status {} else { XCTFail("expected paused") }
    }

    // MARK: After this call

    func testAfterCallFiresWhenCallEnds() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 45)
        s.snoozeUntilAfterCall()
        XCTAssertTrue(s.isWaitingForCall)
        XCTAssertEqual(run(s, minutes: 2), [])
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 3), [])
        XCTAssertEqual(s.status, .postponed(.call))
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 1), [1])
        XCTAssertFalse(s.isWaitingForCall)
    }

    func testAfterCallWaitsForLongCallsPastTheFallback() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.snoozeUntilAfterCall()
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 40), [])
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 1), [1])
    }

    func testAfterCallFallsBackWhenNoCallStarts() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.snoozeUntilAfterCall()
        XCTAssertEqual(run(s, minutes: 20), [15])
    }

    func testAfterCallIgnoresFullScreen() async {
        var config = SchedulerConfig()
        config.postponeDuringFullScreen = false
        let s = makeScheduler(start: ConformanceSupport.monday(10), config: config)
        s.snoozeUntilAfterCall()
        busy.reasons = [.fullScreen]
        XCTAssertEqual(run(s, minutes: 5), [])
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 5), [])  // still waiting for a call
        XCTAssertTrue(s.isWaitingForCall)
    }

    func testAfterCallWorksEvenWhenCallPostponingIsOff() async {
        var config = SchedulerConfig()
        config.postponeDuringCalls = false
        let s = makeScheduler(start: ConformanceSupport.monday(10), config: config)
        s.snoozeUntilAfterCall()
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 30), [])
        busy.reasons = []
        XCTAssertEqual(run(s, minutes: 1), [1])
        // Back to normal: calls no longer hold reminders.
        busy.reasons = [.call]
        XCTAssertEqual(run(s, minutes: 45), [45])
    }

    func testRegularSnoozeCancelsAfterCall() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.snoozeUntilAfterCall()
        s.snooze(minutes: 10)
        XCTAssertFalse(s.isWaitingForCall)
        XCTAssertEqual(run(s, minutes: 10), [10])
    }

    func testQuietHourHoldsRemindersAndRestartsAfter() async {
        var config = SchedulerConfig()
        config.quietHours = [10]
        let s = makeScheduler(start: ConformanceSupport.monday(9, 30), config: config)
        XCTAssertFalse(s.isWithinActiveHours(ConformanceSupport.monday(10, 15)))
        XCTAssertTrue(s.isWithinActiveHours(ConformanceSupport.monday(11)))
        // Due at 10:15, but 10:00–11:00 is quiet. A fresh 45 min starts at 11:00.
        XCTAssertEqual(run(s, minutes: 30), [])
        XCTAssertEqual(s.status, .outsideActiveHours)
        XCTAssertEqual(run(s, minutes: 105), [105])
    }

    // MARK: Jitter

    func testZeroJitterNeverConsultsTheRandomSource() async {
        let random = CountingRandom(onRead: { XCTFail("the random source was read with zero jitter") })
        clock = MockClock(ConformanceSupport.monday(10))
        idle = MockIdle()
        busy = MockBusy()
        let s = ReminderScheduler(
            config: SchedulerConfig(), clock: clock, idle: idle, busy: busy, calendar: calendar, random: random)
        XCTAssertEqual(run(s, minutes: 100), [45, 90])
        s.snooze(minutes: 5)
        s.resume()
        s.sessionCompleted()
        s.pause(for: 60)
        _ = run(s, minutes: 2)
        XCTAssertEqual(random.reads, 0)
    }

    func testJitterOffsetsEveryIntervalFromTheSeed() async {
        var config = SchedulerConfig()
        config.interval = 30 * 60
        config.intervalJitter = 10 * 60
        clock = MockClock(ConformanceSupport.monday(10))
        idle = MockIdle()
        busy = MockBusy()
        let s = ReminderScheduler(
            config: config, clock: clock, idle: idle, busy: busy, calendar: calendar, random: SplitMix64(seed: 42))
        // SplitMix64(42) first draws 0.74156…: floor((0.4831… × 600) + 0.5) = 290 s past the 30 minutes.
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10).addingTimeInterval(30 * 60 + 290))
        // Then −408 s (10:35 + 23:12) and −266 s (10:59 + 25:34), each from the firing tick.
        XCTAssertEqual(run(s, minutes: 100), [35, 59, 85])
    }

    func testJitterBoundsAndRounding() async {
        var config = SchedulerConfig()
        config.interval = 30 * 60
        config.intervalJitter = 10 * 60
        clock = MockClock(ConformanceSupport.monday(10))
        idle = MockIdle()
        busy = MockBusy()
        let low = ReminderScheduler(
            config: config, clock: clock, idle: idle, busy: busy, calendar: calendar, random: ScriptedRandom([0]))
        XCTAssertEqual(low.nextFireDate, ConformanceSupport.monday(10, 20))
        let middle = ReminderScheduler(
            config: config, clock: clock, idle: idle, busy: busy, calendar: calendar, random: ScriptedRandom([0.5]))
        XCTAssertEqual(middle.nextFireDate, ConformanceSupport.monday(10, 30))
        let high = ReminderScheduler(
            config: config, clock: clock, idle: idle, busy: busy, calendar: calendar, random: ScriptedRandom([0.999_999]))
        XCTAssertEqual(high.nextFireDate, ConformanceSupport.monday(10, 40))
    }

    func testRestoreReplacesTheFreshInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.restore(nextFireDate: ConformanceSupport.monday(10, 20))
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 20))
        XCTAssertEqual(run(s, minutes: 70), [20, 65])
    }

    func testRestoreIsIgnoredForAPastDateWhilePausedAndOutsideActiveHours() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.restore(nextFireDate: ConformanceSupport.monday(9, 50))
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 45))
        s.pause(for: 3600)
        s.restore(nextFireDate: ConformanceSupport.monday(10, 30))
        XCTAssertNil(s.nextFireDate)
        let outside = makeScheduler(start: ConformanceSupport.monday(19))
        outside.restore(nextFireDate: ConformanceSupport.monday(19, 30))
        XCTAssertNil(outside.nextFireDate)
    }

    func testSnoozingADeliveredReminderRemindsInItsMinutes() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        _ = run(s, minutes: 20)
        s.snooze(minutes: 10)
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 45), "nothing to answer: snooze never brings the reminder closer")
        s.reminderDelivered()
        s.snooze(minutes: 10)
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 30))
    }

    func testUpcomingRemindersFollowTheInterval() async {
        let s = makeScheduler(start: ConformanceSupport.monday(9, 30))
        XCTAssertEqual(
            s.upcomingReminders(from: ConformanceSupport.monday(9, 30), until: ConformanceSupport.monday(12)),
            [ConformanceSupport.monday(10, 15), ConformanceSupport.monday(11), ConformanceSupport.monday(11, 45)])
        XCTAssertEqual(
            s.upcomingReminders(from: ConformanceSupport.monday(9, 30), until: ConformanceSupport.monday(12), limit: 2),
            [ConformanceSupport.monday(10, 15), ConformanceSupport.monday(11)])
        XCTAssertEqual(s.nextFireDate, ConformanceSupport.monday(10, 15), "planning leaves the scheduler as it was")
    }

    func testUpcomingRemindersSkipToTheNextActiveDay() async {
        let s = makeScheduler(start: ConformanceSupport.monday(17))
        let tuesday = { (h: Int, m: Int) in ConformanceSupport.monday(h, m).addingTimeInterval(86_400) }
        XCTAssertEqual(
            s.upcomingReminders(from: ConformanceSupport.monday(17), until: tuesday(11, 0)),
            [ConformanceSupport.monday(17, 45), tuesday(9, 45), tuesday(10, 30)])
    }

    func testUpcomingRemindersStartAfterAPause() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.pause(until: ConformanceSupport.monday(11))
        XCTAssertEqual(
            s.upcomingReminders(from: ConformanceSupport.monday(10), until: ConformanceSupport.monday(13)),
            [ConformanceSupport.monday(11, 45), ConformanceSupport.monday(12, 30)])
    }

    func testUpcomingRemindersFollowARestoredDate() async {
        let s = makeScheduler(start: ConformanceSupport.monday(10))
        s.restore(nextFireDate: ConformanceSupport.monday(10, 5))
        XCTAssertEqual(
            s.upcomingReminders(from: ConformanceSupport.monday(10), until: ConformanceSupport.monday(11)),
            [ConformanceSupport.monday(10, 5), ConformanceSupport.monday(10, 50)])
    }

    func testJitterIsClampedSoTheIntervalNeverDropsBelowASecond() async {
        var config = SchedulerConfig()
        config.interval = 60
        config.intervalJitter = 10 * 60
        clock = MockClock(ConformanceSupport.monday(10))
        idle = MockIdle()
        busy = MockBusy()
        // The ±10 minutes clamp to ±59 s: floor(−59 + 0.5) = −59 at the bottom, floor(58.99… + 0.5) = 59 at the top.
        let low = ReminderScheduler(
            config: config, clock: clock, idle: idle, busy: busy, calendar: calendar, random: ScriptedRandom([0]))
        XCTAssertEqual(low.nextFireDate, ConformanceSupport.monday(10).addingTimeInterval(1))
        let high = ReminderScheduler(
            config: config, clock: clock, idle: idle, busy: busy, calendar: calendar, random: ScriptedRandom([0.999_999]))
        XCTAssertEqual(high.nextFireDate, ConformanceSupport.monday(10).addingTimeInterval(119))
    }
}
