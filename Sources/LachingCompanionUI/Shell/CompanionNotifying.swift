#if os(macOS)
    import Foundation

    /// What `CompanionAppModel` needs from a configured notification service, so tests can count permission requests.
    /// `ConfiguredCompanionNotifier` provides the production implementation with the app's title and buttons. Actions
    /// arrive as their raw ids, so the package needs no enum of the app's.
    @MainActor
    public protocol CompanionNotifying: AnyObject {
        /// A button was pressed: its action id.
        var onAction: ((String) -> Void)? { get set }
        /// Registers the category and takes the delegate; once at launch.
        func configure()
        /// Asks the user. Only called when the notification channel is turned on.
        func requestAuthorization(completion: @escaping @MainActor (Bool) -> Void)
        func refreshAuthorization(completion: @escaping @MainActor (NotificationPermission) -> Void)
        /// Shows the line (both parts of a joke on two lines), replacing the last banner.
        func post(line: String)
    }
#endif
