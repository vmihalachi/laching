import Foundation
import LachingCore

/// The pure composition root: runs the scheduler on each tick, turns what happened into a trigger, asks the
/// rules, picks a line and hands the shell a `Delivery`. Time comes from the clock, system state from the
/// shell's `Snapshot`, randomness from a seeded source, so a whole day of the companion replays exactly
/// (`conformance/companion/quiet`). The app injects its bank and its language; purchases are not its business (the
/// app keeps `settings.skin` to a skin the user may use). Never sleeps, never touches the system. Isolated to the main
/// actor, like the scheduler and the settings it holds: the shell creates and calls it there.
@MainActor
public final class CompanionCoordinator {
    /// Welcome-back lines are at most this far apart.
    nonisolated public static let welcomeBackCooldown: TimeInterval = 30 * 60
    /// How many recent ids are remembered (the picker skips the last `PhrasePicker.recentWindow`).
    nonisolated public static let recentCapacity = 24

    public let settings: CompanionSettings
    public let bank: PhraseBank
    /// The app's language (BCP-47): phrases that don't exist in it are never picked. Set it when the app's
    /// language changes.
    public var language: String
    public private(set) var scheduler: ReminderScheduler
    public private(set) var recentIDs: [String] = []
    public private(set) var lastDelivery: Delivery?
    /// Lines the shell can't say right now (pending a recording in the voice in use); never picked.
    public var unavailableIDs: Set<String> = []
    public private(set) var lastWelcomeAt: Date?
    private let clock: Clock
    private let calendar: Calendar
    private var random: RandomSource
    private var lastSnapshot: Snapshot?

    /// `seed` drives both the scheduler's jitter (seed) and the picks (seed + 1); pass a fixed one to replay.
    public init(
        settings: CompanionSettings, bank: PhraseBank, language: String, clock: Clock, idle: IdleTimeProvider,
        busy: BusyStateProvider, calendar: Calendar, seed: UInt64 = UInt64.random(in: 0...UInt64.max)
    ) {
        self.settings = settings
        self.bank = bank
        self.language = language
        self.clock = clock
        self.calendar = calendar
        random = SplitMix64(seed: seed &+ 1)
        scheduler = ReminderScheduler(
            config: ChatterCadence.config(for: settings), clock: clock, idle: idle, busy: busy, calendar: calendar,
            random: SplitMix64(seed: seed))
        if let until = settings.shushedUntil {
            if until > clock.now {
                scheduler.pause(until: until)
            } else {
                settings.shushedUntil = nil
            }
        }
    }

    // MARK: Ticks and triggers

    /// Call every 20 s with a fresh snapshot. Returns a line to deliver, or nil.
    @discardableResult
    public func tick(snapshot: Snapshot) -> Delivery? {
        let snapshot = heeded(snapshot)
        let now = clock.now
        // The stored pause, not `isShushed`: the clock may already be past the end before this tick clears it.
        let wasShushed = scheduler.pausedUntil != nil
        // A line held for a call comes back as the after-call line; one held for full screen, as a plain due line.
        let heldForCall = scheduler.status == .postponed(.call)
        let cameBack = cameBack(from: lastSnapshot, to: snapshot)
        lastSnapshot = snapshot

        let decision = scheduler.tick()
        let shushEnded = wasShushed && scheduler.pausedUntil == nil
        if shushEnded {
            settings.shushedUntil = nil
        }
        switch decision {
        case .remind:
            return deliver(heldForCall ? .afterCall : .due, snapshot: snapshot, now: now)
        case .postpone:
            return nil
        case .none:
            break
        }
        if shushEnded {
            return deliver(.shushEnd, snapshot: snapshot, now: now)
        }
        if cameBack, !isShushed {
            if let last = lastWelcomeAt, now.timeIntervalSince(last) < Self.welcomeBackCooldown { return nil }
            return deliver(.welcomeBack, snapshot: snapshot, now: now)
        }
        return nil
    }

    /// Say something: the toy's button. Restarts the chatter interval. Answers even while a line is being spoken: the
    /// shell cuts that line before it performs this one (`QuietRules`).
    public func poke(snapshot: Snapshot) -> Delivery? {
        scheduler.sessionCompleted()
        return deliver(.buttonPress, snapshot: heeded(snapshot), now: clock.now)
    }

    /// Something the shell noticed: battery, charger, appearance.
    public func event(_ trigger: Trigger, snapshot: Snapshot) -> Delivery? {
        guard !isShushed else { return nil }
        return deliver(trigger, snapshot: heeded(snapshot), now: clock.now)
    }

    // MARK: Shh

    /// True until the pause ends (the stored end, so it agrees with `mood` even between ticks).
    public var isShushed: Bool {
        guard let until = scheduler.pausedUntil else { return false }
        return clock.now < until
    }

    public func shush(_ shush: ChatterCadence.Shush) {
        switch shush {
        case .hour: scheduler.pause(for: 60 * 60)
        case .restOfToday: scheduler.pause(until: scheduler.endOfToday)
        case .untilISay: scheduler.pause(until: .distantFuture)
        }
        settings.shushedUntil = scheduler.pausedUntil
    }

    /// Ends Shh early. The character says one line about it.
    public func wake(snapshot: Snapshot) -> Delivery? {
        guard isShushed else { return nil }
        scheduler.resume()
        settings.shushedUntil = nil
        return deliver(.shushEnd, snapshot: heeded(snapshot), now: clock.now)
    }

    // MARK: Settings and state

    /// Call after a timing setting changed (the shell debounces).
    public func settingsChanged() {
        scheduler.update(config: ChatterCadence.config(for: settings))
    }

    public func mood(snapshot: Snapshot) -> Mood {
        CompanionState.mood(status: scheduler.status, snapshot: heeded(snapshot))
    }

    // MARK: Private

    /// The snapshot as the settings want it read: a full-screen app only counts when "hush in full screen" is on.
    private func heeded(_ snapshot: Snapshot) -> Snapshot {
        guard !settings.hushInFullScreen else { return snapshot }
        var heeded = snapshot
        heeded.busy.remove(.fullScreen)
        return heeded
    }

    private func cameBack(from previous: Snapshot?, to current: Snapshot) -> Bool {
        guard let previous else { return false }
        return previous.isAway && !current.isAway
    }

    private func deliver(_ trigger: Trigger, snapshot: Snapshot, now: Date) -> Delivery? {
        guard trigger == .buttonPress || scheduler.isWithinActiveHours(now) else { return nil }
        guard case .deliver(let plan) = QuietRules.decide(trigger: trigger, snapshot: snapshot, manners: settings.manners) else {
            return nil
        }
        let components = calendar.dateComponents([.hour, .weekday], from: now)
        let context = PhraseContext(
            hour: components.hour ?? 0, weekday: components.weekday ?? 1, event: trigger.tag,
            chimeEnabled: plan.chime, recentIDs: recentIDs, flavors: settings.phraseFlavors,
            excludedIDs: settings.mutedPhraseIDs.union(unavailableIDs), skin: settings.skin, language: language)
        guard let phrase = PhrasePicker.pick(from: bank, context: context, unit: random.nextUnit()) else { return nil }
        recentIDs.append(phrase.id)
        if recentIDs.count > Self.recentCapacity { recentIDs.removeFirst(recentIDs.count - Self.recentCapacity) }
        if trigger == .welcomeBack { lastWelcomeAt = now }
        let delivery = Delivery(phrase: phrase, trigger: trigger, plan: plan)
        lastDelivery = delivery
        return delivery
    }
}
