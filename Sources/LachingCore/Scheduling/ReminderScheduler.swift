import Foundation

/// Pure reminder scheduling. Call `tick()` periodically (every 20–30 s).
///
/// Fires a recurring reminder inside the active hours and weekdays, skips quiet hours, treats being idle as a
/// break that restarts the interval, holds a due reminder while the user is busy (a call, a full-screen app)
/// and releases it after a grace period, and supports snooze, "after my next call" and pause. Time comes from
/// the injected `Clock`, user state from the injected providers and any randomness (interval jitter) from the
/// injected `RandomSource`, so every decision is reproducible: `conformance/scheduler` pins it.
public final class ReminderScheduler {
    public private(set) var config: SchedulerConfig
    private let clock: Clock
    private let idle: IdleTimeProvider
    private let busy: BusyStateProvider
    private var random: RandomSource
    public var calendar: Calendar

    public private(set) var nextFireDate: Date?
    public private(set) var pausedUntil: Date?
    public private(set) var isAway = false
    /// A reminder came due while busy and fires once the user is free.
    public private(set) var isHolding = false
    /// "After this call" snooze is on: calls hold the reminder even if that setting is off.
    public private(set) var isAfterCallArmed = false
    /// "After this call" snooze is waiting for a call to start.
    public var isWaitingForCall: Bool { isAfterCallArmed && !isHolding }
    /// The camera or mic is in use right now, so a menu can offer "After this call".
    public var isInCall: Bool { busy.currentBusyReasons().contains(.call) }
    /// A reminder fired and nothing has answered it yet (start, skip, snooze, pause, resume, away).
    public private(set) var hasUnansweredReminder = false
    private var lastBusyAt: Date?
    private var lastBusyReason: BusyReason = .call

    public init(
        config: SchedulerConfig, clock: Clock, idle: IdleTimeProvider, busy: BusyStateProvider = NeverBusy(),
        calendar: Calendar = .current, random: RandomSource = SystemRandom()
    ) {
        self.config = config
        self.clock = clock
        self.idle = idle
        self.busy = busy
        self.calendar = calendar
        self.random = random
        if isWithinActiveHours(clock.now) {
            nextFireDate = clock.now.addingTimeInterval(nextInterval())
        }
    }

    public func update(config: SchedulerConfig) {
        let intervalChanged = config.interval != self.config.interval
        self.config = config
        let now = clock.now
        if intervalChanged || nextFireDate == nil {
            clearBusyState()
            nextFireDate = isWithinActiveHours(now) ? now.addingTimeInterval(nextInterval()) : nil
        }
    }

    public var status: SchedulerStatus {
        let now = clock.now
        if let p = pausedUntil, now < p { return .paused(until: p) }
        if !isWithinActiveHours(now) { return .outsideActiveHours }
        if isHolding { return .postponed(lastBusyReason) }
        if isAway { return .away }
        // A read-only fallback, so it uses the plain interval and never consumes a random number.
        return .scheduled(nextFireDate ?? now.addingTimeInterval(config.interval))
    }

    public func isWithinActiveHours(_ date: Date) -> Bool {
        let weekday = calendar.component(.weekday, from: date)
        guard config.activeWeekdays.contains(weekday) else { return false }
        let comps = calendar.dateComponents([.hour, .minute], from: date)
        if config.quietHours.contains(comps.hour ?? 0) { return false }
        let minute = (comps.hour ?? 0) * 60 + (comps.minute ?? 0)
        if config.activeStartMinute <= config.activeEndMinute {
            return minute >= config.activeStartMinute && minute < config.activeEndMinute
        }
        // Overnight window, e.g. 22:00 - 06:00.
        return minute >= config.activeStartMinute || minute < config.activeEndMinute
    }

    @discardableResult
    public func tick() -> SchedulerDecision {
        let now = clock.now

        if let p = pausedUntil {
            if now < p { return .none }
            pausedUntil = nil
            nextFireDate = now.addingTimeInterval(nextInterval())
        }

        guard isWithinActiveHours(now) else {
            nextFireDate = nil
            isAway = false
            clearBusyState()
            return .none
        }

        // Entering the active window: start a fresh interval.
        guard let fire = nextFireDate else {
            nextFireDate = now.addingTimeInterval(nextInterval())
            return .none
        }

        // Busy: a call or full-screen app isn't a break, so idle doesn't reset the timer.
        var watched = config.postponeReasons
        if isAfterCallArmed { watched.insert(.call) }
        let reasons = busy.currentBusyReasons().intersection(watched)
        if !reasons.isEmpty {
            lastBusyAt = now
            lastBusyReason = reasons.contains(.call) ? .call : .fullScreen
            isAway = false
            if (isWaitingForCall && reasons.contains(.call)) || now >= fire {
                isHolding = true
            }
            return isHolding ? .postpone : .none
        }

        // Away from the desk counts as a break: keep pushing the reminder out.
        if idle.secondsSinceLastInput() >= config.idleThreshold {
            isAway = true
            clearBusyState()
            nextFireDate = now.addingTimeInterval(nextInterval())
            return .none
        }
        isAway = false

        if isHolding {
            if let last = lastBusyAt, now.timeIntervalSince(last) < config.busyGrace { return .postpone }
            return fireReminder(at: now)
        }
        if now >= fire {
            return fireReminder(at: now)
        }
        return .none
    }

    private func fireReminder(at now: Date) -> SchedulerDecision {
        clearBusyState()
        hasUnansweredReminder = true
        nextFireDate = now.addingTimeInterval(nextInterval())
        return .remind
    }

    private func clearBusyState() {
        isHolding = false
        isAfterCallArmed = false
        lastBusyAt = nil
        hasUnansweredReminder = false
    }

    /// The next interval: `config.interval` plus a whole-second random offset within ±`intervalJitter`, the jitter
    /// clamped to `interval − 1` so the next interval is never shorter than a second. With zero jitter the random
    /// source is never consulted, so the committed vectors stay exact.
    private func nextInterval() -> TimeInterval {
        nextInterval(using: &random)
    }

    private func nextInterval(using rng: inout RandomSource) -> TimeInterval {
        guard config.intervalJitter > 0 else { return config.interval }
        let jitter = min(config.intervalJitter, max(config.interval - 1, 0))
        let unit = rng.nextUnit()
        let offset = ((unit * 2 - 1) * jitter + 0.5).rounded(.down)
        return config.interval + offset
    }

    /// Up to `limit` future reminder fire dates in `(from, until]`, advancing a copy of the scheduler
    /// state through active-hour windows, quiet hours and `pausedUntil` assuming no idle and no busy input.
    public func upcomingReminders(from: Date, until: Date, limit: Int = 64) -> [Date] {
        guard limit > 0, until > from, config.interval > 0, !config.activeWeekdays.isEmpty else { return [] }
        var rng = random
        var cursor = from
        var paused = pausedUntil
        var fire: Date?
        if paused == nil && isWithinActiveHours(from) {
            fire = nextFireDate.flatMap { $0 > from ? $0 : nil } ?? from.addingTimeInterval(nextInterval(using: &rng))
        }

        var results: [Date] = []
        results.reserveCapacity(min(limit, 64))

        while results.count < limit && cursor <= until {
            if let p = paused {
                if cursor < p {
                    if p > until { break }
                    cursor = p
                }
                paused = nil
                fire = isWithinActiveHours(cursor) ? cursor.addingTimeInterval(nextInterval(using: &rng)) : nil
                continue
            }

            if !isWithinActiveHours(cursor) {
                guard let nextActive = firstMinute(after: cursor, upTo: until, where: isWithinActiveHours) else { break }
                cursor = nextActive
                fire = cursor.addingTimeInterval(nextInterval(using: &rng))
                continue
            }

            let target = fire ?? cursor.addingTimeInterval(nextInterval(using: &rng))
            fire = target

            if let inactive = firstInactiveDate(after: cursor, upTo: min(target, until)) {
                fire = nil
                cursor = inactive
                continue
            }

            if target > until { break }
            results.append(target)
            cursor = target
            fire = target.addingTimeInterval(nextInterval(using: &rng))
        }
        return results
    }

    private func nextMinuteBoundary(after date: Date) -> Date {
        Date(timeIntervalSinceReferenceDate: (floor(date.timeIntervalSinceReferenceDate / 60) + 1) * 60)
    }

    /// The first whole minute after `date`, up to `limit`, where `test` holds.
    private func firstMinute(after date: Date, upTo limit: Date, where test: (Date) -> Bool) -> Date? {
        var probe = nextMinuteBoundary(after: date)
        while probe <= limit {
            if test(probe) { return probe }
            probe = probe.addingTimeInterval(60)
        }
        return nil
    }

    /// The first inactive minute after `date`, or `limit` itself when it is inactive.
    private func firstInactiveDate(after date: Date, upTo limit: Date) -> Date? {
        firstMinute(after: date, upTo: limit, where: { !isWithinActiveHours($0) }) ?? (isWithinActiveHours(limit) ? nil : limit)
    }

    /// Answering a reminder (unanswered, held during a call, or already snoozed until after a call)
    /// reminds again in `minutes`. Otherwise snooze only ever pushes the next reminder later, never closer.
    public func snooze(minutes: Int = 10) {
        snooze(for: TimeInterval(minutes * 60))
    }

    /// `snooze(minutes:)` for a duration in seconds, for a snooze that isn't whole minutes (say 90 seconds).
    public func snooze(for duration: TimeInterval) {
        let snoozed = clock.now.addingTimeInterval(duration)
        let answering = hasUnansweredReminder || isHolding || isAfterCallArmed
        pausedUntil = nil
        clearBusyState()
        nextFireDate = answering ? snoozed : max(snoozed, nextFireDate ?? snoozed)
    }

    /// Remind shortly after the current (or next) call ends. If no call starts
    /// within `afterCallFallback`, remind then, like a longer snooze.
    public func snoozeUntilAfterCall() {
        pausedUntil = nil
        clearBusyState()
        isAfterCallArmed = true
        nextFireDate = clock.now.addingTimeInterval(config.afterCallFallback)
    }

    public func pause(for duration: TimeInterval) {
        pause(until: clock.now.addingTimeInterval(duration))
    }

    public func pause(until date: Date) {
        pausedUntil = date
        clearBusyState()
        nextFireDate = nil
    }

    /// Start of the next calendar day, for "Pause for the rest of today".
    public var endOfToday: Date {
        let start = calendar.startOfDay(for: clock.now)
        return calendar.date(byAdding: .day, value: 1, to: start) ?? start.addingTimeInterval(86_400)
    }

    public func resume() {
        pausedUntil = nil
        clearBusyState()
        nextFireDate = clock.now.addingTimeInterval(nextInterval())
    }

    /// A session finished (or a reminder was skipped): restart the full interval.
    public func sessionCompleted() {
        clearBusyState()
        nextFireDate = clock.now.addingTimeInterval(nextInterval())
    }

    /// Picks up the next reminder an earlier run of the app scheduled, for an app the system ends between reminders
    /// (on iOS, pre-scheduled notifications fire instead): `date` replaces the interval drawn at creation, so a
    /// relaunch doesn't push the reminder out. Ignored while paused, outside the active hours and for a date not
    /// after now.
    public func restore(nextFireDate date: Date) {
        let now = clock.now
        guard pausedUntil == nil, isWithinActiveHours(now), date > now else { return }
        nextFireDate = date
    }

    /// A reminder went out without `tick()`: the system delivered one the app scheduled ahead (`upcomingReminders`).
    /// Until it's answered, `snooze` reminds again in its minutes, as after a reminder `tick()` fired.
    public func reminderDelivered() {
        hasUnansweredReminder = true
    }
}
