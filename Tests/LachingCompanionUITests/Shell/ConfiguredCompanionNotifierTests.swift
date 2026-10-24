#if os(macOS)
    import Foundation
    @testable import LachingCompanionUI
    import UserNotifications
    import XCTest

    @MainActor
    final class ConfiguredCompanionNotifierTests: XCTestCase {
        private enum Action: String { case more = "MORE", quiet = "QUIET" }

        private func make(_ transport: RecordingTransport) -> ConfiguredCompanionNotifier<Action> {
            ConfiguredCompanionNotifier(
                title: "Localized title",
                notifier: CompanionNotifier<Action>(categoryID: "CATEGORY", lineID: "line", poster: transport))
        }

        func testConfigureAndPostNeverAskPermissionAndPreserveTheBannerIdentity() {
            let transport = RecordingTransport()
            let notifier: any CompanionNotifying = make(transport)
            notifier.configure()
            notifier.post(line: "Setup\nPunchline")
            notifier.post(line: "Next line")
            XCTAssertEqual(transport.configurations, 1)
            XCTAssertTrue(transport.authorizationRequests.isEmpty)
            XCTAssertEqual(transport.posts.map(\.id), ["line", "line"])
            XCTAssertEqual(transport.posts.map(\.title), ["Localized title", "Localized title"])
            XCTAssertEqual(transport.posts.map(\.body), ["Setup\nPunchline", "Next line"])
            XCTAssertEqual(transport.posts.map(\.categoryID), ["CATEGORY", "CATEGORY"])
            XCTAssertTrue(transport.posts.allSatisfy { !$0.sound })
        }

        func testOnlyRecognizedActionsReachTheModelsRawIDHandler() {
            let transport = RecordingTransport()
            let notifier = make(transport)
            var actions: [String] = []
            notifier.onAction = { actions.append($0) }
            transport.onAction?("CATEGORY", "MORE")
            transport.onAction?("CATEGORY", "QUIET")
            transport.onAction?("CATEGORY", "unknown")
            XCTAssertEqual(actions, ["MORE", "QUIET"])
        }

        func testPermissionQueriesAndExplicitRequestsKeepTheirSemantics() async {
            let transport = RecordingTransport()
            let notifier = make(transport)
            for (status, wanted) in [
                (UNAuthorizationStatus.notDetermined, NotificationPermission.notDetermined),
                (.denied, .denied), (.authorized, .authorized), (.provisional, .authorized),
            ] {
                transport.status = status
                let permission = await withCheckedContinuation { continuation in
                    notifier.refreshAuthorization { continuation.resume(returning: $0) }
                }
                XCTAssertEqual(permission, wanted)
                XCTAssertTrue(transport.authorizationRequests.isEmpty)
            }
            for granted in [false, true] {
                transport.granted = granted
                let answer = await withCheckedContinuation { continuation in
                    notifier.requestAuthorization { continuation.resume(returning: $0) }
                }
                XCTAssertEqual(answer, granted)
            }
            XCTAssertEqual(transport.authorizationRequests, [[.alert, .sound], [.alert, .sound]])
        }

        func testTransportCallbackDoesNotRetainTheNotifier() {
            let transport = RecordingTransport()
            weak var weakNotifier: ConfiguredCompanionNotifier<Action>?
            do {
                let notifier = make(transport)
                weakNotifier = notifier
                XCTAssertNotNil(weakNotifier)
            }
            XCTAssertNil(weakNotifier)
            transport.onAction?("CATEGORY", "MORE")
        }
    }

    @MainActor
    private final class RecordingTransport: CompanionNotificationTransport {
        struct Post {
            let id: String
            let title: String
            let body: String
            let categoryID: String
            let sound: Bool
        }
        var onAction: ((String, String) -> Void)?
        var configurations = 0
        var authorizationRequests: [UNAuthorizationOptions] = []
        var status: UNAuthorizationStatus = .notDetermined
        var granted = false
        var posts: [Post] = []

        func configure() { configurations += 1 }
        func requestAuthorization(options: UNAuthorizationOptions) async -> Bool {
            authorizationRequests.append(options)
            return granted
        }
        func authorizationStatus() async -> UNAuthorizationStatus { status }
        func post(id: String, title: String, body: String, categoryID: String, sound: Bool) {
            posts.append(Post(id: id, title: title, body: body, categoryID: categoryID, sound: sound))
        }
    }
#endif
