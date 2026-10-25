#if os(macOS) || os(iOS)
    #if os(macOS)
        import AppKit
    #else
        import UIKit
    #endif
    import Foundation
    import SwiftUI

    /// Where an app's feedback goes: the maker's email address and the App Store's write-a-review page. Opens them and
    /// copies the address; never sends anything itself. `ReviewLifecycle` (LachingStore) decides when to ask.
    public struct AppFeedback: Equatable, Sendable {
        public var email: String
        /// The app's Apple ID in App Store Connect (digits only).
        public var appStoreID: String

        public init(email: String, appStoreID: String) {
            self.email = email
            self.appStoreID = appStoreID
        }

        public var reviewURL: URL { URL(string: "https://apps.apple.com/app/id\(appStoreID)?action=write-review")! }
        public var mailURL: URL { URL(string: "mailto:" + email)! }

        /// Opens the App Store on the app's review page.
        @MainActor public func openReview() {
            #if os(macOS)
                NSWorkspace.shared.open(reviewURL)
            #else
                UIApplication.shared.open(reviewURL)
            #endif
        }

        @MainActor public func copyEmail() {
            #if os(macOS)
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(email, forType: .string)
            #else
                UIPasteboard.general.string = email
            #endif
        }
    }

    /// The words of `ContactAction`'s fallback alert, already localized by the app.
    public struct ContactCopy {
        /// The alert's title ("Contact us").
        public var title: Text
        /// The button that copies the address.
        public var copyAddress: Text
        public var cancel: Text

        public init(title: Text, copyAddress: Text, cancel: Text) {
            self.title = title
            self.copyAddress = copyAddress
            self.cancel = cancel
        }
    }

    /// A button that writes an email to `feedback.email`. When no mail app takes it, an alert shows the address with a
    /// button that copies it. The label is the app's.
    public struct ContactAction<Label: View>: View {
        let feedback: AppFeedback
        let copy: ContactCopy
        let label: () -> Label
        @Environment(\.openURL) private var openURL
        @State private var unavailable = false

        public init(feedback: AppFeedback, copy: ContactCopy, @ViewBuilder label: @escaping () -> Label) {
            self.feedback = feedback
            self.copy = copy
            self.label = label
        }

        public var body: some View {
            Button {
                openURL(feedback.mailURL) { accepted in unavailable = !accepted }
            } label: {
                label()
            }
            .alert(copy.title, isPresented: $unavailable) {
                Button {
                    feedback.copyEmail()
                } label: {
                    copy.copyAddress
                }
                Button(role: .cancel) {
                } label: {
                    copy.cancel
                }
            } message: {
                Text(verbatim: feedback.email)
            }
        }
    }
#endif
