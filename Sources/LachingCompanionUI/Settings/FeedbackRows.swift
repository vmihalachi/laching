#if os(macOS)
    import LachingShell
    import LachingStore
    import SwiftUI

    /// Every word of the feedback rows and the review invitation, already localized by the app. No defaults.
    public struct FeedbackCopy {
        /// "Enjoying the app?", before the review button in both places.
        public var enjoying: Text
        public var leaveReview: Text
        /// The invitation's dismissal, which stops it for good.
        public var noThanks: Text
        /// The row whose button writes an email to the maker.
        public var questions: Text
        /// That button, and the title of the alert shown when no mail app takes the email.
        public var contactUs: Text
        /// The alert's button that copies the address, and its cancel.
        public var copyAddress: Text
        public var cancel: Text

        public init(enjoying: Text, leaveReview: Text, noThanks: Text, questions: Text, contactUs: Text, copyAddress: Text, cancel: Text) {
            self.enjoying = enjoying
            self.leaveReview = leaveReview
            self.noThanks = noThanks
            self.questions = questions
            self.contactUs = contactUs
            self.copyAddress = copyAddress
            self.cancel = cancel
        }

        public var contact: ContactCopy { ContactCopy(title: contactUs, copyAddress: copyAddress, cancel: cancel) }
    }

    /// Settings' feedback rows, for the app's own section and header: "Enjoying the app?" with a review button, then
    /// the maker's address with a button that writes to it (`ContactAction`). The button style is the app's;
    /// `leaveReview` stops the `ReviewLifecycle` and opens the review page.
    public struct FeedbackRows<Style: ButtonStyle>: View {
        let feedback: AppFeedback
        let copy: FeedbackCopy
        let buttonStyle: Style
        let leaveReview: () -> Void
        @Environment(\.companionPalette) private var palette

        public init(feedback: AppFeedback, copy: FeedbackCopy, buttonStyle: Style, leaveReview: @escaping () -> Void) {
            self.feedback = feedback
            self.copy = copy
            self.buttonStyle = buttonStyle
            self.leaveReview = leaveReview
        }

        public var body: some View {
            HStack {
                copy.enjoying
                Spacer()
                Button(action: leaveReview) { copy.leaveReview }.buttonStyle(buttonStyle)
            }
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    copy.questions
                    Text(verbatim: feedback.email)
                        .font(.system(size: 12))
                        .foregroundStyle(palette.inkSoft)
                        .textSelection(.enabled)
                }
                Spacer()
                ContactAction(feedback: feedback, copy: copy.contact) { copy.contactUs }.buttonStyle(buttonStyle)
            }
        }
    }

    /// Home's quiet ask while `review.isDue`: a last row under a hairline, a review button and a dismissal that stops it
    /// for good. Nothing else ever asks. The button styles are the app's; `leaveReview` stops the lifecycle and opens
    /// the review page.
    public struct ReviewInvitation<Quiet: ButtonStyle, Outline: ButtonStyle>: View {
        let review: ReviewLifecycle
        let copy: FeedbackCopy
        let quiet: Quiet
        let outline: Outline
        let leaveReview: () -> Void
        @Environment(\.companionPalette) private var palette

        public init(review: ReviewLifecycle, copy: FeedbackCopy, quiet: Quiet, outline: Outline, leaveReview: @escaping () -> Void) {
            self.review = review
            self.copy = copy
            self.quiet = quiet
            self.outline = outline
            self.leaveReview = leaveReview
        }

        public var body: some View {
            if review.isDue {
                Rectangle().fill(palette.line).frame(height: 1).accessibilityHidden(true)
                HStack(spacing: Space.snug) {
                    copy.enjoying
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button {
                        review.stop()
                    } label: {
                        copy.noThanks
                    }.buttonStyle(quiet)
                    Button(action: leaveReview) { copy.leaveReview }.buttonStyle(outline)
                }
            }
        }
    }
#endif
