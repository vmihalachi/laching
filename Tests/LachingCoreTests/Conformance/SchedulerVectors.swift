import Foundation
import LachingCore
import LachingTesting

/// `conformance/scheduler/*.json`: a config, a timeline of steps (ticks with idle and busy state,
/// user actions), and the scheduler's expected decisions and state after each step.
enum SchedulerVectors {
    static let directory = "scheduler"

    // MARK: Format

    struct Config: Codable, Equatable {
        var intervalSeconds: Int
        var activeStartMinute: Int
        var activeEndMinute: Int
        /// 1 = Sunday … 7 = Saturday, in the vector's time zone.
        var activeWeekdays: [Int]
        var quietHours: [Int]
        var idleThresholdSeconds: Int
        var postponeDuringCalls: Bool
        var postponeDuringFullScreen: Bool
        var busyGraceSeconds: Int
        var afterCallFallbackSeconds: Int
        /// Only present when non-zero, so older vectors stay byte-identical.
        var intervalJitterSeconds: Int?

        init(_ c: SchedulerConfig) {
            intervalSeconds = Int(c.interval)
            activeStartMinute = c.activeStartMinute
            activeEndMinute = c.activeEndMinute
            activeWeekdays = c.activeWeekdays.sorted()
            quietHours = c.quietHours.sorted()
            idleThresholdSeconds = Int(c.idleThreshold)
            postponeDuringCalls = c.postponeDuringCalls
            postponeDuringFullScreen = c.postponeDuringFullScreen
            busyGraceSeconds = Int(c.busyGrace)
            afterCallFallbackSeconds = Int(c.afterCallFallback)
            intervalJitterSeconds = c.intervalJitter > 0 ? Int(c.intervalJitter) : nil
        }

        var scheduler: SchedulerConfig {
            SchedulerConfig(
                interval: TimeInterval(intervalSeconds), activeStartMinute: activeStartMinute,
                activeEndMinute: activeEndMinute, activeWeekdays: Set(activeWeekdays),
                quietHours: Set(quietHours), idleThreshold: TimeInterval(idleThresholdSeconds),
                postponeDuringCalls: postponeDuringCalls, postponeDuringFullScreen: postponeDuringFullScreen,
                busyGrace: TimeInterval(busyGraceSeconds),
                afterCallFallback: TimeInterval(afterCallFallbackSeconds),
                intervalJitter: TimeInterval(intervalJitterSeconds ?? 0))
        }
    }

    struct Status: Codable, Equatable {
        /// "scheduled" (with `date`), "paused" (with `date`, the end), "outsideActiveHours", "away",
        /// or "postponed" (with `reason`: "call" or "fullScreen").
        var type: String
        var date: String?
        var reason: String?
    }

    struct State: Codable, Equatable {
        var now: String
        var status: Status
        var nextFireDate: String?
        var pausedUntil: String?
        var isAway: Bool
        var isHolding: Bool
        var isWaitingForCall: Bool
        var endOfToday: String

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(now, forKey: .now)
            try c.encode(status, forKey: .status)
            try c.encode(nextFireDate, forKey: .nextFireDate)
            try c.encode(pausedUntil, forKey: .pausedUntil)
            try c.encode(isAway, forKey: .isAway)
            try c.encode(isHolding, forKey: .isHolding)
            try c.encode(isWaitingForCall, forKey: .isWaitingForCall)
            try c.encode(endOfToday, forKey: .endOfToday)
        }
    }

    struct TickDecision: Codable, Equatable {
        /// 1-based tick within the step.
        var tick: Int
        /// "remind" or "postpone". Ticks not listed returned "none".
        var decision: String
    }

    struct Expect: Codable, Equatable {
        var decisions: [TickDecision]?
        var result: Bool?
        var state: State
    }

    /// One step. `op` is one of:
    /// - "ticks": `count` times, advance the clock by `everySeconds` (0 = don't), then tick with
    ///   `idleSeconds` since the last input and `busy` reasons ("call", "fullScreen").
    /// - "setTime": set the clock to `time`.
    /// - "snooze" (`minutes`), "snoozeFor" (`seconds`), "snoozeUntilAfterCall", "pauseFor" (`seconds`), "pauseUntil" (`time`),
    ///   "pauseUntilEndOfToday", "resume", "sessionCompleted", "updateConfig" (`config`),
    ///   "restore" (`time`, the next fire date an earlier run scheduled), "reminderDelivered".
    /// - "isWithinActiveHours": query `time`; `expect.result` is the answer.
    struct Step: Codable, Equatable {
        var op: String
        var count: Int?
        var everySeconds: Int?
        var idleSeconds: Int?
        var busy: [String]?
        var time: String?
        var minutes: Int?
        var seconds: Int?
        var config: Config?
        var expect: Expect?
    }

    struct Vector: Codable, Equatable {
        var name: String
        var description: String
        var timeZone: String
        /// The clock when the scheduler is created.
        var start: String
        var config: Config
        /// The SplitMix64 seed for the scheduler's random source; only present when the vector uses jitter.
        var seed: UInt64?
        /// State right after creation.
        var initialState: State?
        var steps: [Step]
    }

    // MARK: Reference evaluation

    /// `SnapshotData.busyReason(_:)`, throwing on a name the vectors don't know.
    static func busyReason(_ name: String) throws -> BusyReason {
        guard let reason = SnapshotData.busyReason(name) else { throw ConformanceSupport.VectorError("unknown busy reason \(name)") }
        return reason
    }

    static func decisionName(_ d: SchedulerDecision) -> String {
        switch d {
        case .none: "none"
        case .remind: "remind"
        case .postpone: "postpone"
        }
    }

    /// Runs the vector's inputs through `ReminderScheduler` and returns it with every
    /// expectation filled in from the Swift reference.
    @MainActor
    static func evaluate(_ vector: Vector) throws -> Vector {
        let calendar = try ConformanceSupport.calendar(vector.timeZone)
        let tz = calendar.timeZone
        let clock = MockClock(try ConformanceSupport.date(vector.start))
        let idle = MockIdle()
        let busy = MockBusy()
        let scheduler = ReminderScheduler(
            config: vector.config.scheduler, clock: clock, idle: idle, busy: busy,
            calendar: calendar, random: SplitMix64(seed: vector.seed ?? 0))
        func iso(_ d: Date?) -> String? { d.map { ConformanceSupport.string($0, in: tz) } }
        func snapshot() -> State {
            let status: Status
            switch scheduler.status {
            case .scheduled(let d): status = Status(type: "scheduled", date: iso(d))
            case .paused(let until): status = Status(type: "paused", date: iso(until))
            case .outsideActiveHours: status = Status(type: "outsideActiveHours")
            case .away: status = Status(type: "away")
            case .postponed(let r): status = Status(type: "postponed", reason: SnapshotData.name(r))
            }
            return State(
                now: iso(clock.now)!, status: status, nextFireDate: iso(scheduler.nextFireDate),
                pausedUntil: iso(scheduler.pausedUntil), isAway: scheduler.isAway,
                isHolding: scheduler.isHolding, isWaitingForCall: scheduler.isWaitingForCall,
                endOfToday: iso(scheduler.endOfToday)!)
        }
        func required<T>(_ value: T?, _ field: String, _ op: String) throws -> T {
            guard let value else { throw ConformanceSupport.VectorError("\(vector.name): \(op) needs \(field)") }
            return value
        }

        var out = vector
        out.initialState = snapshot()
        for (index, step) in vector.steps.enumerated() {
            var expect = Expect(state: snapshot())
            switch step.op {
            case "ticks":
                idle.idle = TimeInterval(step.idleSeconds ?? 0)
                busy.reasons = Set(try (step.busy ?? []).map(busyReason))
                var decisions: [TickDecision] = []
                for n in 1...(try required(step.count, "count", step.op)) {
                    clock.now = clock.now.addingTimeInterval(TimeInterval(step.everySeconds ?? 0))
                    let d = scheduler.tick()
                    if d != .none { decisions.append(TickDecision(tick: n, decision: decisionName(d))) }
                }
                expect.decisions = decisions
            case "setTime":
                clock.now = try ConformanceSupport.date(try required(step.time, "time", step.op))
            case "snooze":
                scheduler.snooze(minutes: try required(step.minutes, "minutes", step.op))
            case "snoozeFor":
                scheduler.snooze(for: TimeInterval(try required(step.seconds, "seconds", step.op)))
            case "snoozeUntilAfterCall":
                scheduler.snoozeUntilAfterCall()
            case "pauseFor":
                scheduler.pause(for: TimeInterval(try required(step.seconds, "seconds", step.op)))
            case "pauseUntil":
                scheduler.pause(until: try ConformanceSupport.date(try required(step.time, "time", step.op)))
            case "pauseUntilEndOfToday":
                scheduler.pause(until: scheduler.endOfToday)
            case "resume":
                scheduler.resume()
            case "sessionCompleted":
                scheduler.sessionCompleted()
            case "updateConfig":
                scheduler.update(config: try required(step.config, "config", step.op).scheduler)
            case "restore":
                scheduler.restore(nextFireDate: try ConformanceSupport.date(try required(step.time, "time", step.op)))
            case "reminderDelivered":
                scheduler.reminderDelivered()
            case "isWithinActiveHours":
                expect.result = scheduler.isWithinActiveHours(try ConformanceSupport.date(try required(step.time, "time", step.op)))
            default:
                throw ConformanceSupport.VectorError("\(vector.name): unknown op \(step.op) at step \(index)")
            }
            expect.state = snapshot()
            out.steps[index].expect = expect
        }
        return out
    }

    // MARK: Cases

    /// Builds a timeline. `idle` and `busy` apply to the ticks that follow, like the mocks in
    /// `ReminderSchedulerTests`.
    struct Script {
        let calendar: Calendar
        var steps: [Step] = []
        var idle = 0
        var busy: [BusyReason] = []

        /// Monday 2026-09-28 at the given local time.
        func monday(_ hour: Int, _ minute: Int = 0) -> Date {
            ConformanceSupport.local(calendar, 2026, 9, 28, hour, minute)
        }

        func iso(_ date: Date) -> String { ConformanceSupport.string(date, in: calendar.timeZone) }

        /// Ticks once a minute, like `ReminderSchedulerTests.run`.
        mutating func run(minutes: Int) { ticks(minutes, every: 60) }

        mutating func ticks(_ count: Int, every seconds: Int) {
            steps.append(
                Step(
                    op: "ticks", count: count, everySeconds: seconds, idleSeconds: idle,
                    busy: busy.map(SnapshotData.name).sorted()))
        }

        /// One tick without moving the clock.
        mutating func tickNow() { ticks(1, every: 0) }

        mutating func setTime(_ date: Date) { steps.append(Step(op: "setTime", time: iso(date))) }
        mutating func snooze(minutes: Int) { steps.append(Step(op: "snooze", minutes: minutes)) }
        mutating func snooze(seconds: Int) { steps.append(Step(op: "snoozeFor", seconds: seconds)) }
        mutating func snoozeUntilAfterCall() { steps.append(Step(op: "snoozeUntilAfterCall")) }
        mutating func pause(seconds: Int) { steps.append(Step(op: "pauseFor", seconds: seconds)) }
        mutating func pause(until date: Date) { steps.append(Step(op: "pauseUntil", time: iso(date))) }
        mutating func pauseUntilEndOfToday() { steps.append(Step(op: "pauseUntilEndOfToday")) }
        mutating func resume() { steps.append(Step(op: "resume")) }
        mutating func sessionCompleted() { steps.append(Step(op: "sessionCompleted")) }
        mutating func update(_ config: SchedulerConfig) { steps.append(Step(op: "updateConfig", config: Config(config))) }
        mutating func reminderDelivered() { steps.append(Step(op: "reminderDelivered")) }
        mutating func restore(_ date: Date) { steps.append(Step(op: "restore", time: iso(date))) }
        mutating func isWithinActiveHours(_ date: Date) { steps.append(Step(op: "isWithinActiveHours", time: iso(date))) }
    }

    struct Case: Sendable {
        var name: String
        var description: String
        var timeZone = "UTC"
        var config = SchedulerConfig()
        var seed: UInt64?
        var start: @Sendable (Script) -> Date
        var build: @Sendable (inout Script) -> Void
    }

    static func config(_ change: (inout SchedulerConfig) -> Void) -> SchedulerConfig {
        var c = SchedulerConfig()
        change(&c)
        return c
    }

    static let cases: [Case] = [
        Case(
            name: "remind-after-interval", description: "Default config: a reminder every 45 minutes.",
            start: { $0.monday(9, 30) }
        ) { s in s.run(minutes: 100) },
        Case(
            name: "custom-interval", description: "A 20-minute interval.",
            config: config { $0.interval = 20 * 60 }, start: { $0.monday(10) }
        ) { s in s.run(minutes: 60) },
        Case(
            name: "idle-resets-timer",
            description: "Being away for longer than the idle threshold counts as a break and restarts the interval.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 30)
            s.idle = 10 * 60
            s.run(minutes: 30)
            s.idle = 0
            s.run(minutes: 50)
        },
        Case(
            name: "short-idle-is-not-away", description: "Idle under the threshold doesn't count as away.",
            start: { $0.monday(10) }
        ) { s in
            s.idle = 2 * 60
            s.run(minutes: 45)
        },
        Case(
            name: "outside-active-hours", description: "No reminders after the active window closes at 18:00.",
            start: { $0.monday(17, 30) }
        ) { s in
            s.run(minutes: 29)
            s.run(minutes: 1)
            s.run(minutes: 300)
        },
        Case(
            name: "entering-active-hours", description: "Entering the active window starts a fresh interval.",
            start: { $0.monday(8) }
        ) { s in s.run(minutes: 110) },
        Case(
            name: "weekend", description: "No reminders on inactive weekdays (Saturday).",
            start: { ConformanceSupport.local($0.calendar, 2026, 9, 26, 10) }
        ) { s in s.run(minutes: 200) },
        Case(
            name: "overnight-window", description: "An active window that crosses midnight (22:00–06:00, every day).",
            config: config {
                $0.activeStartMinute = 22 * 60
                $0.activeEndMinute = 6 * 60
                $0.activeWeekdays = Set(1...7)
            }, start: { $0.monday(21, 30) }
        ) { s in
            s.isWithinActiveHours(s.monday(12))
            s.isWithinActiveHours(s.monday(23))
            s.isWithinActiveHours(s.monday(2))
            s.isWithinActiveHours(s.monday(6))
            s.run(minutes: 120)
        },
        Case(
            name: "snooze", description: "Snooze delays the next reminder by the given minutes.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 45)
            s.snooze(minutes: 10)
            s.run(minutes: 15)
        },
        Case(
            name: "snooze-seconds", description: "A snooze in seconds reminds again after exactly that long, here 90 seconds.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 45)
            s.snooze(seconds: 90)
            s.ticks(10, every: 15)
        },
        Case(
            name: "snooze-without-reminder",
            description: "With no reminder to answer, snooze never brings the next one closer, only pushes it later.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 20)
            s.snooze(minutes: 10)
            s.run(minutes: 20)
            s.snooze(minutes: 10)
            s.run(minutes: 15)
        },
        Case(
            name: "snooze-after-answered-reminder",
            description: "Once a reminder is answered (here by a finished session), snooze leaves the next one alone.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 45)
            s.sessionCompleted()
            s.snooze(minutes: 10)
            s.run(minutes: 50)
        },
        Case(
            name: "snooze-held-reminder",
            description: "Snoozing a reminder held during a call reminds 10 minutes later.",
            start: { $0.monday(10) }
        ) { s in
            s.busy = [.call]
            s.run(minutes: 50)
            s.snooze(minutes: 10)
            s.busy = []
            s.run(minutes: 15)
        },
        Case(
            name: "pause-for", description: "A pause suppresses reminders, then a full interval starts when it ends.",
            start: { $0.monday(10) }
        ) { s in
            s.pause(seconds: 3600)
            s.run(minutes: 60)
            s.run(minutes: 50)
        },
        Case(
            name: "resume", description: "Resume ends a pause early and restarts the interval.",
            start: { $0.monday(10) }
        ) { s in
            s.pause(seconds: 3600)
            s.run(minutes: 10)
            s.resume()
            s.run(minutes: 45)
        },
        Case(
            name: "pause-until-across-days", description: "A pause until a date lasts across days, then restarts the interval.",
            start: { $0.monday(10) }
        ) { s in
            let wednesday = ConformanceSupport.local(s.calendar, 2026, 9, 30)
            s.pause(until: wednesday)
            s.setTime(s.calendar.date(byAdding: .hour, value: 30, to: s.monday(10))!)
            s.tickNow()
            s.setTime(ConformanceSupport.local(s.calendar, 2026, 9, 30, 10))
            s.tickNow()
            s.run(minutes: 50)
        },
        Case(
            name: "pause-until-end-of-today", description: "\"Pause for the rest of today\" lasts until the next midnight.",
            start: { $0.monday(23, 30) }
        ) { s in
            s.pauseUntilEndOfToday()
            s.setTime(s.monday(23, 59))
            s.tickNow()
            s.run(minutes: 5)
        },
        Case(
            name: "session-completed", description: "Finishing a session restarts the full interval.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 40)
            s.sessionCompleted()
            s.run(minutes: 50)
        },
        Case(
            name: "changing-interval", description: "Changing the interval reschedules from now.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 10)
            s.update(config { $0.interval = 15 * 60 })
            s.run(minutes: 16)
        },
        Case(
            name: "call-postpones-then-grace",
            description: "A call holds a due reminder; it fires once the call has been over for the grace period.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 40)
            s.busy = [.call]
            s.run(minutes: 20)
            s.tickNow()
            s.busy = []
            s.ticks(2, every: 20)
            s.run(minutes: 46)
        },
        Case(
            name: "busy-before-due", description: "Being busy before the reminder is due doesn't change the schedule.",
            start: { $0.monday(10) }
        ) { s in
            s.busy = [.fullScreen]
            s.run(minutes: 30)
            s.busy = []
            s.run(minutes: 15)
        },
        Case(
            name: "postpone-only-when-held", description: "Ticks return postpone only while a reminder is held.",
            start: { $0.monday(10) }
        ) { s in
            s.busy = [.call]
            s.ticks(1, every: 10 * 60)
            s.ticks(1, every: 40 * 60)
        },
        Case(
            name: "disabled-busy-reasons", description: "Busy reasons whose setting is off are ignored.",
            config: config { $0.postponeDuringFullScreen = false }, start: { $0.monday(10) }
        ) { s in
            s.busy = [.fullScreen]
            s.run(minutes: 45)
            s.update(
                config {
                    $0.postponeDuringFullScreen = false
                    $0.postponeDuringCalls = false
                })
            s.busy = [.call]
            s.run(minutes: 45)
        },
        Case(
            name: "call-wins-over-full-screen", description: "With both reasons, the status reports the call.",
            start: { $0.monday(10) }
        ) { s in
            s.busy = [.call, .fullScreen]
            s.run(minutes: 46)
        },
        Case(
            name: "idle-during-call", description: "Idle during a call isn't a break: the held reminder fires after the call.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 30)
            s.busy = [.call]
            s.idle = 20 * 60
            s.run(minutes: 30)
            s.busy = []
            s.idle = 0
            s.run(minutes: 1)
        },
        Case(
            name: "walking-away-after-call", description: "Walking away after a call drops the held reminder.",
            start: { $0.monday(10) }
        ) { s in
            s.busy = [.call]
            s.run(minutes: 50)
            s.busy = []
            s.idle = 10 * 60
            s.run(minutes: 5)
            s.idle = 0
            s.run(minutes: 46)
        },
        Case(
            name: "held-reminder-dropped-at-end-of-hours", description: "A held reminder is dropped when active hours end.",
            start: { $0.monday(17) }
        ) { s in
            s.busy = [.call]
            s.run(minutes: 65)
            s.busy = []
            s.run(minutes: 120)
        },
        Case(
            name: "busy-outside-active-hours", description: "Being busy outside active hours does nothing.",
            start: { $0.monday(19) }
        ) { s in
            s.busy = [.call]
            s.tickNow()
        },
        Case(
            name: "pause-wins-over-busy", description: "Pausing drops a held reminder.",
            start: { $0.monday(10) }
        ) { s in
            s.busy = [.call]
            s.run(minutes: 50)
            s.pause(seconds: 3600)
            s.busy = []
            s.run(minutes: 59)
        },
        Case(
            name: "after-call-fires-when-call-ends", description: "\"After this call\" fires once the next call ends.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 45)
            s.snoozeUntilAfterCall()
            s.run(minutes: 2)
            s.busy = [.call]
            s.run(minutes: 3)
            s.busy = []
            s.run(minutes: 1)
        },
        Case(
            name: "after-call-long-call", description: "\"After this call\" waits for a call that lasts past the fallback.",
            start: { $0.monday(10) }
        ) { s in
            s.snoozeUntilAfterCall()
            s.busy = [.call]
            s.run(minutes: 40)
            s.busy = []
            s.run(minutes: 1)
        },
        Case(
            name: "after-call-fallback", description: "\"After this call\" fires after the fallback when no call starts.",
            start: { $0.monday(10) }
        ) { s in
            s.snoozeUntilAfterCall()
            s.run(minutes: 20)
        },
        Case(
            name: "after-call-ignores-full-screen", description: "\"After this call\" waits for a call, not a full-screen app.",
            config: config { $0.postponeDuringFullScreen = false }, start: { $0.monday(10) }
        ) { s in
            s.snoozeUntilAfterCall()
            s.busy = [.fullScreen]
            s.run(minutes: 5)
            s.busy = []
            s.run(minutes: 5)
        },
        Case(
            name: "after-call-with-call-postponing-off",
            description: "\"After this call\" works even when postponing during calls is off, then calls stop holding reminders.",
            config: config { $0.postponeDuringCalls = false }, start: { $0.monday(10) }
        ) { s in
            s.snoozeUntilAfterCall()
            s.busy = [.call]
            s.run(minutes: 30)
            s.busy = []
            s.run(minutes: 1)
            s.busy = [.call]
            s.run(minutes: 45)
        },
        Case(
            name: "snooze-cancels-after-call", description: "A regular snooze cancels \"After this call\".",
            start: { $0.monday(10) }
        ) { s in
            s.snoozeUntilAfterCall()
            s.snooze(minutes: 10)
            s.run(minutes: 10)
        },
        Case(
            name: "quiet-hour", description: "A quiet hour holds reminders; a fresh interval starts when it ends.",
            config: config { $0.quietHours = [10] }, start: { $0.monday(9, 30) }
        ) { s in
            s.isWithinActiveHours(s.monday(10, 15))
            s.isWithinActiveHours(s.monday(11))
            s.run(minutes: 30)
            s.run(minutes: 105)
        },
        Case(
            name: "local-weekday-at-midnight",
            description: "Weekdays and hours are local: Monday 00:00 in Bucharest is still Sunday in UTC.",
            timeZone: "Europe/Bucharest",
            config: config {
                $0.activeStartMinute = 0
                $0.activeEndMinute = 2 * 60
                $0.activeWeekdays = [2]
            }, start: { ConformanceSupport.local($0.calendar, 2026, 9, 27, 23, 30) }
        ) { s in s.run(minutes: 180) },
        Case(
            name: "dst-fall-back-quiet-hour",
            description: "Daylight saving ends in Bucharest at 04:00 on 2026-10-25, so the quiet 03:00 hour happens twice.",
            timeZone: "Europe/Bucharest",
            config: config {
                $0.activeStartMinute = 0
                $0.activeEndMinute = 24 * 60
                $0.activeWeekdays = Set(1...7)
                $0.quietHours = [3]
            }, start: { ConformanceSupport.local($0.calendar, 2026, 10, 25, 2) }
        ) { s in s.run(minutes: 240) },
        Case(
            name: "jitter-seeded",
            description: "A ±10 minute jitter on a 30 minute interval, drawn from the seeded SplitMix64 source in whole seconds.",
            config: config {
                $0.interval = 30 * 60
                $0.intervalJitter = 10 * 60
            }, seed: 42, start: { $0.monday(10) }
        ) { s in s.run(minutes: 150) },
        Case(
            name: "jitter-clamped-to-interval",
            description: "A ±10 minute jitter on a 1 minute interval is clamped to ±59 s: no interval shorter than a second.",
            config: config {
                $0.interval = 60
                $0.intervalJitter = 10 * 60
            }, seed: 42, start: { $0.monday(10) }
        ) { s in s.run(minutes: 20) },
        Case(
            name: "jitter-zero-is-exact",
            description: "With no jitter the interval is exact and the random source is never consulted, whatever the seed.",
            config: config { $0.interval = 30 * 60 }, seed: 7, start: { $0.monday(10) }
        ) { s in s.run(minutes: 61) },
        Case(
            name: "restore-next-fire-date",
            description: "A relaunch restores the reminder the earlier run scheduled; the interval after it is the usual one.",
            start: { $0.monday(10) }
        ) { s in
            s.restore(s.monday(10, 20))
            s.run(minutes: 70)
        },
        Case(
            name: "restore-ignored",
            description: "Restoring is ignored for a date not after now, while paused and outside the active hours.",
            start: { $0.monday(10) }
        ) { s in
            s.restore(s.monday(10))
            s.restore(s.monday(9, 50))
            s.pause(seconds: 3600)
            s.restore(s.monday(10, 30))
            s.resume()
            s.setTime(s.monday(18, 30))
            s.tickNow()
            s.restore(s.monday(18, 45))
        },
        Case(
            name: "reminder-delivered-snooze",
            description: "A reminder the system delivered on its own is unanswered: snoozing it reminds again in 10 minutes.",
            start: { $0.monday(10) }
        ) { s in
            s.run(minutes: 20)
            s.snooze(minutes: 10)
            s.reminderDelivered()
            s.snooze(minutes: 10)
            s.run(minutes: 15)
        },
    ]

    @MainActor
    static func vector(for c: Case) throws -> Vector {
        var script = Script(calendar: try ConformanceSupport.calendar(c.timeZone))
        c.build(&script)
        let input = Vector(
            name: c.name, description: c.description, timeZone: c.timeZone,
            start: script.iso(c.start(script)), config: Config(c.config), seed: c.seed, steps: script.steps)
        return try evaluate(input)
    }

    @MainActor
    static func files() throws -> [String: Data] {
        var out: [String: Data] = [:]
        for c in cases { out["\(directory)/\(c.name).json"] = try ConformanceSupport.encode(try vector(for: c)) }
        return out
    }

    /// Replays a committed vector and returns the differences from the reference.
    @MainActor
    static func verify(_ data: Data, file: String) throws -> [String] {
        let committed = try ConformanceSupport.decode(Vector.self, from: data)
        let actual = try evaluate(committed)
        var problems: [String] = []
        if actual.initialState != committed.initialState { problems.append("\(file): initialState differs") }
        for (n, (a, b)) in zip(actual.steps, committed.steps).enumerated() where a.expect != b.expect {
            problems.append("\(file): step \(n) (\(b.op)) expected \(String(describing: b.expect)), got \(String(describing: a.expect))")
        }
        return problems
    }
}
