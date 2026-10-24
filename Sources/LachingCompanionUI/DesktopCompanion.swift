#if os(macOS)
    import AppKit
    import LachingCompanion
    import LachingShell
    import SwiftUI

    /// The character on the desktop: a `FloatingWindow` with the app's character view standing on a soft contact shadow,
    /// and a second one riding along beside it for the speech bubble. A click says something, a drag moves the character
    /// (and reports the spot), a right click opens its menu, a pinch changes the size and a click on the bubble puts it
    /// away. The app decides when it shows (`DesktopPresence`) and what it says. Needs the main actor; asks for no
    /// permission.
    @MainActor
    public final class DesktopCompanion {
        /// A click on the character.
        public var onSay: (() -> Void)?
        /// A drag ended at a new spot.
        public var onMoved: ((DesktopPosition) -> Void)?
        /// A trackpad pinch: +1 bigger, -1 smaller.
        public var onPinch: ((Int) -> Void)?
        /// A click on the bubble put it away.
        public var onBubbleDismissed: (() -> Void)?
        /// The right-click menu.
        public var menu: (() -> NSMenu)?

        /// Default spot: this far in from the bottom right of the primary screen's visible frame.
        public static let cornerInset: CGFloat = 24
        /// The desktop bubble's text size.
        public static let bubbleTextSize: CGFloat = 15

        public let layout: DesktopLayout
        private let character: FloatingWindow
        private let bubble: FloatingWindow
        private let environment: (AnyView) -> AnyView
        private var size: DesktopSize
        private var position: DesktopPosition?
        private var bubbleText: String?
        private var countdown: BubbleCountdown?

        /// `character` is the app's view of the character (a `CharacterView` bound to its state); `sayActionName` names
        /// the VoiceOver action that says something. `palette` and `displayFont` reach the bubble, which lives in a
        /// window of its own.
        public init(
            character content: AnyView, layout: DesktopLayout, size: DesktopSize, position: DesktopPosition?,
            sayActionName: String, palette: CompanionPalette = .system,
            displayFont: @escaping @Sendable (CGFloat) -> Font = EnvironmentValues().companionDisplayFont
        ) {
            self.layout = layout
            self.size = size
            self.position = position
            environment = { view in
                AnyView(view.environment(\.companionPalette, palette).environment(\.companionDisplayFont, displayFont))
            }
            let windowSize = layout.windowSize(for: size)
            let stand = DesktopStand(content: content, layout: layout, sayActionName: sayActionName)
            character = FloatingWindow(
                content: environment(AnyView(stand)), size: windowSize, anchor: Self.anchor(for: position, size: windowSize))
            bubble = FloatingWindow(content: AnyView(EmptyView()), size: .zero, anchor: .zero, isMovable: false)
            bubble.attach(to: character)
            stand.onSay.action = { [weak self] in self?.onSay?() }
            character.onClick = { [weak self] in self?.onSay?() }
            character.onPinch = { [weak self] step in self?.onPinch?(step) }
            character.menu = { [weak self] in self?.menu?() }
            character.onDragEnded = { [weak self] anchor in self?.dragEnded(at: anchor) }
            character.onScreensChanged = { [weak self] in self?.placeBubble() }
            bubble.onClick = { [weak self] in
                self?.hideBubble(animated: true)
                self?.onBubbleDismissed?()
            }
        }

        public var isShown: Bool { character.isShown }

        public func show(animated: Bool) {
            guard !character.isShown else { return }
            character.show(animated: animated)
        }

        public func hide(animated: Bool) {
            hideBubble(animated: animated)
            character.hide(animated: animated)
        }

        public func setSize(_ size: DesktopSize) {
            guard size != self.size else { return }
            self.size = size
            let windowSize = layout.windowSize(for: size)
            if position == nil { character.anchor = Self.anchor(for: nil, size: windowSize) }
            character.setSize(windowSize)
            placeBubble()
        }

        public func setPosition(_ position: DesktopPosition?) {
            guard position != self.position else { return }
            self.position = position
            character.anchor = Self.anchor(for: position, size: character.size)
            placeBubble()
        }

        // MARK: Bubble

        /// Shows `text` beside the character (only while it's shown); `countdown` runs the dial down.
        public func showBubble(_ text: String, countdown: BubbleCountdown? = nil, animated: Bool) {
            guard character.isShown, text != bubbleText || countdown != self.countdown || !bubble.isShown else { return }
            bubbleText = text
            self.countdown = countdown
            placeBubble()
            bubble.show(animated: animated)
        }

        /// Starts (or clears) the dial on the bubble showing now.
        public func setCountdown(_ countdown: BubbleCountdown?) {
            guard countdown != self.countdown else { return }
            self.countdown = countdown
            placeBubble()
        }

        public func hideBubble(animated: Bool) {
            bubbleText = nil
            countdown = nil
            bubble.hide(animated: animated)
        }

        /// Beside the character's head, on the side with more room, the tail pointing at the character.
        private func placeBubble() {
            guard let text = bubbleText, let screen = character.visibleFrame else { return }
            let parent = character.frame
            let side = FloatingPlacement.side(beside: parent, in: screen)
            let view = environment(
                AnyView(Self.bubblePreview(text: text, tail: side == .left ? .trailing : .leading, countdown: countdown, layout: layout)))
            let ideal = FloatingWindow.idealSize(of: view, maxWidth: layout.bubbleMaxWidth + 2 * layout.bubbleMargin)
            let size = CGSize(width: ceil(ideal.width), height: ceil(ideal.height))
            // Exactly the measured size from the window's top left, and each line a view of its own: the window takes its
            // new size at once, so a card and words animating from the last line's shape would hang out of it.
            bubble.setContent(AnyView(view.frame(width: size.width, height: size.height, alignment: .topLeading).id(text)))
            // The tail's tip level with the bubble pivot (the head's middle).
            let centerY = layout.headY(in: parent) + layout.bubbleTailY(height: size.height) - size.height / 2
            bubble.setFrame(
                FloatingPlacement.frame(
                    size: size, beside: parent, on: side, centerY: centerY, gap: layout.bubbleGap(width: parent.width), in: screen))
        }

        // MARK: The views, for screenshot sheets

        /// The character standing on the desktop the way the companion's window draws it: `character` at the window's
        /// size over a soft contact shadow in the palette's shade.
        public static func stand(character: AnyView, layout: DesktopLayout) -> CompanionStand {
            CompanionStand(character: character, layout: layout)
        }

        /// The desktop bubble the way the companion shows it; `tail` is `.trailing` when it sits left of the character. At
        /// most `bubbleMaxWidth` wide however it's asked, so a screenshot sheet's `.fixedSize()` gets the window's lines.
        public static func bubblePreview(text: String, tail: Edge, countdown: BubbleCountdown?, layout: DesktopLayout) -> some View {
            WidthLimit(width: layout.bubbleMaxWidth) {
                SpeechBubble(text: text, tail: tail, size: bubbleTextSize, fillsWidth: false, countdown: countdown, roomForDial: true)
            }
            .padding(layout.bubbleMargin)
        }

        // MARK: Position

        private func dragEnded(at anchor: CGPoint) {
            placeBubble()
            guard let primary = NSScreen.screens.first else { return }
            let point = FloatingPlacement.topLeftOrigin(anchor, primaryHeight: primary.frame.height)
            let moved = DesktopPosition(x: Int(point.x.rounded()), y: Int(point.y.rounded()))
            position = moved
            onMoved?(moved)
        }

        /// The saved spot in AppKit coordinates, or the bottom right of the primary screen.
        private static func anchor(for position: DesktopPosition?, size: CGSize) -> CGPoint {
            guard let primary = NSScreen.screens.first else { return .zero }
            if let position {
                return FloatingPlacement.fromTopLeftOrigin(CGPoint(x: position.x, y: position.y), primaryHeight: primary.frame.height)
            }
            return FloatingPlacement.defaultAnchor(size: size, in: primary.visibleFrame, inset: cornerInset)
        }
    }

    /// The character standing on the desktop over a soft contact shadow, so it looks like it stands rather than floats:
    /// what the companion's window draws, and a screenshot sheet with it (`DesktopCompanion.stand`).
    public struct CompanionStand: View {
        let character: AnyView
        let layout: DesktopLayout
        @Environment(\.companionPalette) private var palette

        public init(character: AnyView, layout: DesktopLayout) {
            self.character = character
            self.layout = layout
        }

        public var body: some View {
            GeometryReader { proxy in
                ZStack(alignment: .bottom) {
                    Ellipse()
                        .fill(palette.shade)
                        .frame(width: proxy.size.width * layout.shadowWidthRatio, height: layout.shadowHeight * 2)
                        .blur(radius: 1.5)
                    character.padding(.bottom, layout.shadowHeight)
                }
                .frame(width: proxy.size.width, height: proxy.size.height)
            }
        }
    }

    /// The stand in the companion's window. Clicks, drags and the menu are the window's; VoiceOver gets "say something"
    /// as an action.
    private struct DesktopStand: View {
        /// Set after the window exists, so the VoiceOver action reaches the companion.
        final class Action {
            var action: (() -> Void)?
        }

        let content: AnyView
        let layout: DesktopLayout
        let sayActionName: String
        let onSay = Action()

        var body: some View {
            CompanionStand(character: content, layout: layout)
                .accessibilityElement(children: .combine)
                .accessibilityAction(named: Text(verbatim: sayActionName)) { onSay.action?() }
        }
    }

    /// Offers its one view at most `width`, even when asked for an ideal size (`.fixedSize()`), where
    /// `.frame(maxWidth:)` offers no width at all and a `SpeechBubble`'s lines have nothing to even out against.
    private struct WidthLimit: Layout {
        let width: CGFloat

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            subviews.first?.sizeThatFits(offer(proposal)) ?? .zero
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
        }

        private func offer(_ proposal: ProposedViewSize) -> ProposedViewSize {
            ProposedViewSize(width: min(proposal.width ?? width, width), height: proposal.height)
        }
    }
#endif
