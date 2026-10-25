import Foundation
import LachingCore
import Observation

/// When an app may ask for a review, and the promise that it then stops. An ask becomes due after enough use, counted
/// one of two ways: `countDay` for each day the app is opened (due on the `days`th distinct local day), or
/// `countUses` over a history the app keeps (due once it holds `minimum` uses on `days` distinct local days). It stops
/// for good on `stop()` (the person dismissed the ask, or left a review from the app's own link) or on `beginAttempt()`
/// (the one native request, stopped before it is made, so a request the system suppresses or that fails is never
/// retried). Never infers whether someone rated, and never asks by itself: the app shows its invitation while `isDue`,
/// or makes the native request when `beginAttempt()` says so.
///
/// Its keys (`feedback.review.*`) sit beside the app's preferences in `store` but are never among them, so resetting
/// the preferences (Start over) keeps them. `@Observable`: a view that reads `isDue` redraws when it changes.
@Observable
public final class ReviewLifecycle {
    /// Distinct local days of use before an ask is due.
    public let days: Int
    /// An ask may be shown, or the native request attempted. False once stopped.
    public private(set) var isDue: Bool

    @ObservationIgnored private let store: any SettingsStore
    private static let stoppedKey = "feedback.review.stopped.v1"
    private static let daysKey = "feedback.review.homeDays.v1"
    private static let pendingKey = "feedback.review.pending.v1"

    public init(store: any SettingsStore, days: Int = 3) {
        self.store = store
        self.days = days
        isDue = false
        isDue = computeDue()
    }

    /// Stopped for good: dismissed, reviewed from the app's link, or the native request attempted.
    public var isStopped: Bool { store.bool(forKey: Self.stoppedKey) == true }

    /// Counts the local day `date` falls on in `calendar` as a day of use. Keeps at most `days` days.
    public func countDay(_ date: Date, calendar: Calendar) {
        guard !isStopped else { return }
        let parts = calendar.dateComponents([.era, .year, .month, .day], from: date)
        let day = "\(parts.era ?? 0)-\(parts.year ?? 0)-\(parts.month ?? 0)-\(parts.day ?? 0)"
        var counted = Array(Set(store.strings(forKey: Self.daysKey) ?? [])).sorted()
        if counted.count < days && !counted.contains(day) {
            counted.append(day)
            store.set(strings: counted, forKey: Self.daysKey)
        }
        isDue = computeDue()
    }

    /// For an app that keeps a history of uses: due from now on once `dates` (every use, any order) pass
    /// `isEligible`. Call it after each new use; a later, shorter history never takes the ask back.
    public func countUses(_ dates: [Date], minimum: Int, calendar: Calendar) {
        guard !isStopped, Self.isEligible(dates, minimum: minimum, days: days, calendar: calendar) else { return }
        store.set(bool: true, forKey: Self.pendingKey)
        isDue = computeDue()
    }

    /// At least `minimum` uses, on at least `days` distinct local days in `calendar`.
    public static func isEligible(_ dates: [Date], minimum: Int, days: Int, calendar: Calendar) -> Bool {
        dates.count >= minimum && Set(dates.map { calendar.startOfDay(for: $0) }).count >= days
    }

    /// Never asks again. Called when the person dismisses the ask or leaves a review from the app's own link.
    public func stop() {
        store.set(bool: true, forKey: Self.stoppedKey)
        store.remove(forKey: Self.daysKey)
        store.remove(forKey: Self.pendingKey)
        isDue = false
    }

    /// The one native request: true when due, and then already stopped, so the app makes the request exactly once.
    public func beginAttempt() -> Bool {
        guard computeDue() else { return false }
        stop()
        return true
    }

    private func computeDue() -> Bool {
        !isStopped && (store.bool(forKey: Self.pendingKey) == true || (store.strings(forKey: Self.daysKey)?.count ?? 0) >= days)
    }
}
