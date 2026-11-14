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

        /// No launch event is being handled (a test, or a call after `applicationDidFinishLaunching`): the convenience
        /// answers as the rule does without the event, on the login item and the session start alone.
        func testWithoutTheEventTheConvenienceFallsBackToTheRule() {
            XCTAssertFalse(LaunchContext.launchedAsLoginItem())
            XCTAssertEqual(LaunchContext.launchedAtLogin(), LaunchContext.launchedAtLogin(appleEvent: false))
        }

        /// While macOS's login-item open-application event is handled, the convenience reads it itself, so a caller
        /// can't drop it by passing `false`; once handled, it's gone.
        func testTheConvenienceReadsTheLoginItemEventWhileItIsHandled() {
            let manager = NSAppleEventManager.shared()
            let probe = LaunchProbe()
            // `LaunchContext` reads the event's ID, not its class, so the test sends open-application under a class of
            // its own: the host's real open-application handler (AppKit installs one) is never replaced, and removing
            // the test's handler after puts everything back.
            let eventClass: AEEventClass = "lcts".utf8.reduce(0) { $0 << 8 | AEEventClass($1) }
            let eventID = AEEventID(kAEOpenApplication)
            manager.setEventHandler(
                probe, andSelector: #selector(LaunchProbe.handle(_:withReplyEvent:)), forEventClass: eventClass, andEventID: eventID)
            defer { manager.removeEventHandler(forEventClass: eventClass, andEventID: eventID) }

            let event = NSAppleEventDescriptor(
                eventClass: eventClass, eventID: eventID, targetDescriptor: NSAppleEventDescriptor(processIdentifier: getpid()),
                returnID: AEReturnID(kAutoGenerateReturnID), transactionID: AETransactionID(kAnyTransactionID))
            event.setParam(NSAppleEventDescriptor(enumCode: OSType(keyAELaunchedAsLogInItem)), forKeyword: AEKeyword(keyAEPropData))
            guard let raw = event.aeDesc else { return XCTFail("no raw event") }
            var reply = AppleEvent()
            var refCon = 0  // unused by the handler, but the call takes one
            let status = withUnsafeMutablePointer(to: &refCon) {
                manager.dispatchRawAppleEvent(raw, withRawReply: &reply, handlerRefCon: UnsafeMutableRawPointer($0))
            }
            XCTAssertEqual(status, OSErr(noErr))
            AEDisposeDesc(&reply)

            XCTAssertEqual(probe.asLoginItem, [true])
            XCTAssertEqual(probe.atLogin, [true])
            XCTAssertFalse(LaunchContext.launchedAsLoginItem())
        }
    }

    /// Reads `LaunchContext` from inside an Apple Event handler, where AppKit hands the event over.
    private final class LaunchProbe: NSObject {
        var asLoginItem: [Bool] = []
        var atLogin: [Bool] = []

        @objc func handle(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
            asLoginItem.append(LaunchContext.launchedAsLoginItem())
            atLogin.append(LaunchContext.launchedAtLogin())
        }
    }
#endif
