import LachingCore
import LachingStore
import XCTest

final class ReviewLifecycleTests: XCTestCase {
    private var calendar: Calendar {
        var value = Calendar(identifier: .gregorian)
        value.timeZone = TimeZone(secondsFromGMT: -8 * 3600)!
        return value
    }
    private let start = Date(timeIntervalSince1970: 1_770_000_000)
    private func day(_ offset: Int) -> Date { start.addingTimeInterval(Double(offset) * 86_400) }
    /// Five uses on three days.
    private var uses: [Date] { [0, 0, 1, 1, 2].map(day) }

    // MARK: Days of use

    func testDueOnTheThirdDistinctLocalDayWithBoundedStorage() {
        let store = InMemorySettingsStore()
        let review = ReviewLifecycle(store: store)
        for _ in 0..<5 { review.countDay(start, calendar: calendar) }
        XCTAssertFalse(review.isDue)
        review.countDay(day(1), calendar: calendar)
        XCTAssertFalse(review.isDue)
        review.countDay(day(2), calendar: calendar)
        XCTAssertTrue(review.isDue)
        for offset in 3..<10 { review.countDay(day(offset), calendar: calendar) }
        XCTAssertEqual(store.strings(forKey: "feedback.review.homeDays.v1")?.count, 3)
        XCTAssertTrue(ReviewLifecycle(store: store).isDue)
    }

    func testDaysFollowTheCalendarsTimeZone() {
        // 23:30 and 00:30 UTC are one evening at UTC-8, two days at UTC.
        let dates = ["2026-10-01T23:30:00Z", "2026-10-02T00:30:00Z"].map { ISO8601DateFormatter().date(from: $0)! }
        let local = ReviewLifecycle(store: InMemorySettingsStore(), days: 2)
        for date in dates { local.countDay(date, calendar: calendar) }
        XCTAssertFalse(local.isDue)
        var utc = calendar
        utc.timeZone = TimeZone(secondsFromGMT: 0)!
        let utcReview = ReviewLifecycle(store: InMemorySettingsStore(), days: 2)
        for date in dates { utcReview.countDay(date, calendar: utc) }
        XCTAssertTrue(utcReview.isDue)
    }

    func testDismissalSurvivesRelaunchAndForgetsTheDays() {
        let store = InMemorySettingsStore()
        let review = ReviewLifecycle(store: store)
        for offset in 0..<3 { review.countDay(day(offset), calendar: calendar) }
        XCTAssertTrue(review.isDue)
        review.stop()
        XCTAssertFalse(review.isDue)
        let relaunched = ReviewLifecycle(store: store)
        for offset in 0..<10 { relaunched.countDay(day(offset), calendar: calendar) }
        XCTAssertFalse(relaunched.isDue)
        XCTAssertTrue(relaunched.isStopped)
        XCTAssertNil(store.strings(forKey: "feedback.review.homeDays.v1"))
    }

    func testStoppingBeforeEligibilityNeverAsks() {
        let store = InMemorySettingsStore()
        let review = ReviewLifecycle(store: store)
        review.stop()
        for offset in 0..<3 { review.countDay(day(offset), calendar: calendar) }
        review.countUses(uses, minimum: 5, calendar: calendar)
        XCTAssertFalse(review.isDue)
        XCTAssertFalse(ReviewLifecycle(store: store).isDue)
    }

    // MARK: A history of uses

    func testEligibilityNeedsEnoughUsesOnEnoughLocalDays() {
        XCTAssertFalse(ReviewLifecycle.isEligible(Array(uses.prefix(4)), minimum: 5, days: 3, calendar: calendar))
        XCTAssertFalse(ReviewLifecycle.isEligible(Array(repeating: start, count: 5), minimum: 5, days: 3, calendar: calendar))
        XCTAssertFalse(ReviewLifecycle.isEligible([0, 0, 2, 2, 2].map(day), minimum: 5, days: 3, calendar: calendar))
        XCTAssertTrue(ReviewLifecycle.isEligible(uses, minimum: 5, days: 3, calendar: calendar))
        let dates = [
            "2026-10-01T23:30:00Z", "2026-10-02T00:30:00Z", "2026-10-02T12:00:00Z", "2026-10-02T14:00:00Z", "2026-10-03T00:30:00Z",
        ]
        .map { ISO8601DateFormatter().date(from: $0)! }
        XCTAssertFalse(ReviewLifecycle.isEligible(dates, minimum: 5, days: 3, calendar: calendar))
        var utc = calendar
        utc.timeZone = TimeZone(secondsFromGMT: 0)!
        XCTAssertTrue(ReviewLifecycle.isEligible(dates, minimum: 5, days: 3, calendar: utc))
    }

    func testOneAttemptThatSurvivesRelaunch() {
        let store = InMemorySettingsStore()
        let review = ReviewLifecycle(store: store)
        XCTAssertFalse(review.beginAttempt())
        review.countUses(Array(uses.prefix(4)), minimum: 5, calendar: calendar)
        XCTAssertFalse(review.isDue)
        review.countUses(uses, minimum: 5, calendar: calendar)
        XCTAssertTrue(review.isDue)
        // Due stays due when the history later shrinks.
        review.countUses([], minimum: 5, calendar: calendar)
        XCTAssertTrue(ReviewLifecycle(store: store).isDue)
        XCTAssertTrue(review.beginAttempt())
        XCTAssertFalse(review.isDue)
        let relaunched = ReviewLifecycle(store: store)
        relaunched.countUses(uses, minimum: 5, calendar: calendar)
        XCTAssertTrue(relaunched.isStopped)
        XCTAssertFalse(relaunched.beginAttempt())
    }

    func testTheAppsLinkCancelsAPendingRequest() {
        let store = InMemorySettingsStore()
        let review = ReviewLifecycle(store: store)
        review.countUses(uses, minimum: 5, calendar: calendar)
        review.stop()
        review.countUses(uses, minimum: 5, calendar: calendar)
        XCTAssertFalse(review.isDue)
        XCTAssertFalse(review.beginAttempt())
        XCTAssertTrue(ReviewLifecycle(store: store).isStopped)
        XCTAssertNil(store.bool(forKey: "feedback.review.pending.v1"))
    }

    func testEachInstanceReadsTheSharedStore() {
        let store = InMemorySettingsStore()
        ReviewLifecycle(store: store).countUses(uses, minimum: 5, calendar: calendar)
        let other = ReviewLifecycle(store: store)
        XCTAssertTrue(other.beginAttempt())
        XCTAssertFalse(ReviewLifecycle(store: store).beginAttempt())
    }
}
