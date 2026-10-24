#if os(macOS)
    import Foundation

    /// A notification service ready for `CompanionAppModel`: the app supplies its localized title and buttons and
    /// stable category, action and line ids. Routes recognized actions as raw ids. Registers and posts without asking
    /// for permission; only `requestAuthorization` prompts. Needs the main actor and performs no network requests.
    @MainActor
    public final class ConfiguredCompanionNotifier<Action: RawRepresentable<String>>: CompanionNotifying {
        public var onAction: ((String) -> Void)?
        private let notifier: CompanionNotifier<Action>
        private let title: String

        public convenience init(title: String, categoryID: String, actions: [(Action, String)], lineID: String) {
            self.init(title: title, notifier: CompanionNotifier(categoryID: categoryID, actions: actions, lineID: lineID))
        }

        init(title: String, notifier: CompanionNotifier<Action>) {
            self.title = title
            self.notifier = notifier
            notifier.onAction = { [weak self] action in self?.onAction?(action.rawValue) }
        }

        public func configure() {
            notifier.configure()
        }

        public func requestAuthorization(completion: @escaping @MainActor (Bool) -> Void) {
            notifier.requestAuthorization(completion: completion)
        }

        public func refreshAuthorization(completion: @escaping @MainActor (NotificationPermission) -> Void) {
            notifier.refreshAuthorization(completion: completion)
        }

        public func post(line: String) {
            notifier.post(line: line, title: title)
        }
    }
#endif
