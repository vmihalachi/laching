#if os(macOS)
    import LachingShell
    import XCTest

    final class LaunchContextTests: XCTestCase {
        func testTheAppleEventIsEnough() {
            XCTAssertTrue(LaunchContext.isAtLogin(appleEvent: true, loginItem: false, sinceSessionStart: nil))
            XCTAssertTrue(LaunchContext.isAtLogin(appleEvent: true, loginItem: false, sinceSessionStart: 3_600))
        }

        func testALoginItemJustAfterTheSessionStartedIsAtLogin() {
            XCTAssertTrue(LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: 0))
            XCTAssertTrue(LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: 8))
            XCTAssertTrue(
                LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: LaunchContext.loginGrace - 1))
        }

        func testALaterLaunchIsTheUsers() {
            XCTAssertFalse(
                LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: LaunchContext.loginGrace))
            XCTAssertFalse(LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: 3_600))
        }

        func testWithoutTheLoginItemOrTheSessionStartOnlyTheEventCounts() {
            XCTAssertFalse(LaunchContext.isAtLogin(appleEvent: false, loginItem: false, sinceSessionStart: 8))
            XCTAssertFalse(LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: nil))
            XCTAssertFalse(LaunchContext.isAtLogin(appleEvent: false, loginItem: true, sinceSessionStart: -5))
        }
    }
#endif
