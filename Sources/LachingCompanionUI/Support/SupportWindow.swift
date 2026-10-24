#if os(macOS)
    import LachingStore
    import SwiftUI

    /// The Support window: first the one-time supporter purchase (shared with the family), then, once that's bought,
    /// the tips with their App Store prices. One ask at a time, and never right after a purchase: buying the supporter or
    /// a tip leaves only a thank-you until the window is opened again (each open builds a fresh view). Restore brings
    /// the supporter back, so it shows only until the supporter is owned. The app opens it only on the user's request; every word and the look are the app's.
    public struct SupportWindow<Primary: ButtonStyle, Outline: ButtonStyle>: View {
        let purchases: PurchaseStore
        let products: SupportProducts
        let copy: SupportCopy
        let look: SupportLook<Primary, Outline>
        let onDone: () -> Void
        /// The supporter purchase went through (or was restored) while this window was open.
        @State private var justSupported = false
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @Environment(\.companionPalette) private var palette

        public init(
            purchases: PurchaseStore, products: SupportProducts, copy: SupportCopy, look: SupportLook<Primary, Outline>,
            onDone: @escaping () -> Void
        ) {
            self.purchases = purchases
            self.products = products
            self.copy = copy
            self.look = look
            self.onDone = onDone
        }

        /// What the window shows.
        public enum Stage: Equatable, Sendable {
            /// The pitch and the supporter button.
            case pitch
            /// Right after the supporter purchase: the character, glad, and nothing else to buy.
            case welcome
            /// A supporter: thanks, and the tips.
            case supporter
            /// Right after a tip: only the thank-you.
            case tipped
        }

        public static func stage(justSupported: Bool, justTipped: Bool, isSupporter: Bool) -> Stage {
            if justSupported { return .welcome }
            if justTipped { return .tipped }
            return isSupporter ? .supporter : .pitch
        }

        private var isSupporter: Bool { purchases.isOwned(products.supporter) }

        public var body: some View {
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.section) {
                        look.title
                        switch Self.stage(justSupported: justSupported, justTipped: purchases.justTipped, isSupporter: isSupporter) {
                        case .welcome:
                            celebration(copy.welcomeLine, note: copy.welcomeNote)
                        case .tipped:
                            celebration(copy.tipThanksLine, note: nil)
                        case .supporter:
                            aside(copy.supporterThanks)
                            tips
                        case .pitch:
                            aside(copy.pitch)
                            supporter
                        }
                        status
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .windowPadding(bottom: Space.group)
                }
                ActionBar {
                    // Restore brings back only the supporter purchase (tips are consumables): nothing to restore once
                    // it's owned.
                    if !isSupporter {
                        Button {
                            Task { await purchases.restore() }
                        } label: {
                            copy.restore
                        }
                        .buttonStyle(look.outline)
                        .disabled(purchases.isBusy)
                    }
                    Spacer()
                    Button(action: onDone) { copy.done }
                        .buttonStyle(look.primary)
                        .keyboardShortcut(.cancelAction)
                }
            }
            .task { await purchases.loadProductsIfNeeded() }
            .onChange(of: isSupporter) { _, isSupporter in
                if isSupporter { justSupported = true }
            }
            .animation(reduceMotion ? nil : .spring(response: 0.45, dampingFraction: 0.7), value: justSupported)
        }

        /// The character beside `text`.
        private func aside(_ text: Text) -> some View {
            HStack(alignment: .top, spacing: Space.group) {
                look.character(false)
                text
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, Space.item)
            }
        }

        /// The character, large and centered, saying `line`: the whole window after a purchase, so it reads as thanks,
        /// not a shop.
        private func celebration(_ line: String, note: Text?) -> some View {
            VStack(spacing: Space.group) {
                look.bubble(line)
                look.character(true)
                if let note {
                    note
                        .font(.system(size: 12))
                        .foregroundStyle(palette.inkSoft)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.top, Space.group)
            .transition(reduceMotion ? .opacity : .scale(scale: 0.85).combined(with: .opacity))
        }

        private var supporter: some View {
            VStack(alignment: .leading, spacing: Space.item) {
                Button {
                    Task { await purchases.buy(products.supporter) }
                } label: {
                    copy.becomeSupporter(purchases.price(products.supporter))
                }
                .buttonStyle(look.largePrimary)
                .disabled(purchases.isBusy || purchases.price(products.supporter) == nil)
                copy.supporterNote
                    .font(.system(size: 12))
                    .foregroundStyle(palette.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }

        private var tips: some View {
            VStack(alignment: .leading, spacing: Space.item) {
                look.tipsHeader
                HStack(spacing: Space.item) {
                    ForEach(products.tips, id: \.self) { tip in
                        Button {
                            Task { await purchases.tip(tip) }
                        } label: {
                            VStack(spacing: Space.tight) {
                                Text(verbatim: copy.tipTitle(tip))
                                    .multilineTextAlignment(.center)
                                look.tipPrice(Text(verbatim: purchases.price(tip) ?? "…").font(.system(size: 12)))
                            }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                        .buttonStyle(look.largePrimary)
                        .disabled(purchases.isBusy || purchases.price(tip) == nil)
                    }
                }
                // Every card as tall as the tallest, whichever titles wrap.
                .fixedSize(horizontal: false, vertical: true)
                copy.tipsNote
                    .font(.system(size: 12))
                    .foregroundStyle(palette.inkSoft)
            }
        }

        @ViewBuilder
        private var status: some View {
            if let error = purchases.error {
                Text(verbatim: copy.error(error))
                    .foregroundStyle(palette.accentText)
                    .fixedSize(horizontal: false, vertical: true)
            } else if purchases.isPending {
                copy.askToBuy
                    .foregroundStyle(palette.inkSoft)
            } else if purchases.loadState == .failed {
                HStack(spacing: Space.item) {
                    copy.pricesFailed
                        .foregroundStyle(palette.inkSoft)
                    Button {
                        Task { await purchases.loadProducts() }
                    } label: {
                        copy.tryAgain
                    }
                    .buttonStyle(look.outline)
                }
            }
        }
    }
#endif
