#if os(macOS)
    import LachingCompanionUI
    import LachingCore
    import LachingShell
    import LachingStore
    import SwiftUI
    import XCTest

    private struct Plain: ButtonStyle {
        func makeBody(configuration: Configuration) -> some View { configuration.label }
    }

    @MainActor
    final class FeedbackRowsTests: XCTestCase {
        private let copy = FeedbackCopy(
            enjoying: Text(verbatim: "Enjoying?"), leaveReview: Text(verbatim: "Review"), noThanks: Text(verbatim: "No"),
            questions: Text(verbatim: "Questions?"), contactUs: Text(verbatim: "Contact"), copyAddress: Text(verbatim: "Copy"),
            cancel: Text(verbatim: "Cancel"))

        private func height(_ view: some View) -> CGFloat {
            let host = NSHostingView(rootView: VStack(spacing: 0) { view }.frame(width: 400))
            host.layout()
            return host.fittingSize.height
        }

        func testTheInvitationShowsOnlyWhileDueAndItsDismissalStopsIt() {
            let review = ReviewLifecycle(store: InMemorySettingsStore(), days: 1)
            let invitation = ReviewInvitation(
                review: review, copy: copy, quiet: Plain(), outline: Plain(), leaveReview: {})
            XCTAssertEqual(height(invitation), 0)
            review.countDay(Date(), calendar: .current)
            XCTAssertGreaterThan(height(invitation), 0)
            review.stop()
            XCTAssertEqual(height(invitation), 0)
        }

        func testTheRowsDrawBothRows() {
            let rows = FeedbackRows(
                feedback: AppFeedback(email: "hello@example.com", appStoreID: "1"), copy: copy, buttonStyle: Plain(),
                leaveReview: {})
            XCTAssertGreaterThan(height(rows), 30)
        }
    }
#endif
