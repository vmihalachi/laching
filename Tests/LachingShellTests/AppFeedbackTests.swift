#if os(macOS) || os(iOS)
    import LachingShell
    import XCTest

    final class AppFeedbackTests: XCTestCase {
        func testTheReviewPageAndTheMailAddressComeFromTheIDAndEmail() {
            let feedback = AppFeedback(email: "hello@example.com", appStoreID: "1234567890")
            XCTAssertEqual(feedback.reviewURL.absoluteString, "https://apps.apple.com/app/id1234567890?action=write-review")
            XCTAssertEqual(feedback.mailURL.absoluteString, "mailto:hello@example.com")
        }
    }
#endif
