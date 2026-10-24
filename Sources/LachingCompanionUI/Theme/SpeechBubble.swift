#if os(macOS)
    import SwiftUI

    /// A sticker card with a tail toward the character, in the app's display face (`companionDisplayFont`), drawn with the
    /// `companionPalette`. While the character thinks it shows "…". It fills the width it's offered, or hugs its words
    /// when `fillsWidth` is false (wrapping only when they don't fit); wrapped lines come out of even length
    /// (`BalancedWrap`). With a `countdown`, a tiny dial in its bottom-trailing corner runs down until the bubble goes
    /// away; `roomForDial` keeps the words clear of that corner (set it where a countdown can appear, so the size doesn't
    /// change when it does).
    public struct SpeechBubble: View {
        public var text: String?
        public var tail: Edge
        public var size: CGFloat
        public var fillsWidth: Bool
        public var countdown: BubbleCountdown?
        public var roomForDial: Bool
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @Environment(\.companionPalette) private var palette
        @Environment(\.companionDisplayFont) private var displayFont

        public init(
            text: String?, tail: Edge = .leading, size: CGFloat = 16, fillsWidth: Bool = true, countdown: BubbleCountdown? = nil,
            roomForDial: Bool = false
        ) {
            self.text = text
            self.tail = tail
            self.size = size
            self.fillsWidth = fillsWidth
            self.countdown = countdown
            self.roomForDial = roomForDial
        }

        public var body: some View {
            BalancedLines {
                Text(verbatim: text ?? "…")
                    .font(displayFont(size))
                    .foregroundStyle(palette.ink)
                    .multilineTextAlignment(.leading)
            }
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: fillsWidth ? .infinity : nil, alignment: .leading)
            .padding(.horizontal, Space.item)
            .padding(.trailing, roomForDial ? CountdownDial.room : 0)
            .padding(.vertical, Space.snug + 2)
            .overlay(alignment: .bottomTrailing) {
                if let countdown {
                    CountdownDial(countdown: countdown)
                        .id(countdown)
                        .padding([.trailing, .bottom], CountdownDial.inset)
                        .transition(.opacity)
                }
            }
            .background { bubble }
            .padding(Edge.Set(tail), BubbleShape.tailLength)
            .contentTransition(.opacity)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: text)
            .animation(.easeOut(duration: 0.15), value: countdown)
        }

        /// Card and tail are one outline, so the tail joins the card without a seam.
        private var bubble: some View {
            let shape = BubbleShape(tail: tail)
            return ZStack {
                shape.fill(palette.card)
                    .shadow(color: palette.shade, radius: 2, x: 0, y: 2)
                shape.stroke(palette.outline, style: StrokeStyle(lineWidth: 2, lineJoin: .round))
            }
            .accessibilityHidden(true)
        }
    }

    /// When the bubble started running down and for how long.
    public struct BubbleCountdown: Hashable, Sendable {
        public var start: Date
        public var seconds: TimeInterval

        public init(start: Date, seconds: TimeInterval) {
            self.start = start
            self.seconds = seconds
        }
    }

    /// An `accentSoft` ring whose `accent` arc drains clockwise from twelve o'clock as the countdown runs out.
    private struct CountdownDial: View {
        let countdown: BubbleCountdown
        @State private var remaining: CGFloat = 1
        @Environment(\.companionPalette) private var palette

        static let diameter: CGFloat = 10
        /// From the card's bottom and trailing edges.
        static let inset: CGFloat = 9
        /// Extra trailing room for the words, so the dial never sits on them.
        static let room: CGFloat = 11
        private static let lineWidth: CGFloat = 2.5

        var body: some View {
            ZStack {
                Circle().stroke(palette.accentSoft, lineWidth: Self.lineWidth)
                Circle()
                    .trim(from: 1 - remaining, to: 1)
                    .stroke(palette.accent, style: StrokeStyle(lineWidth: Self.lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            .padding(Self.lineWidth / 2)
            .frame(width: Self.diameter, height: Self.diameter)
            .accessibilityHidden(true)
            .onAppear {
                let left = max(0, countdown.seconds - Date().timeIntervalSince(countdown.start))
                remaining = countdown.seconds > 0 ? CGFloat(left / countdown.seconds) : 0
                withAnimation(.linear(duration: left)) { remaining = 0 }
            }
        }
    }

    /// The bubble's outline: a rounded card (drawn 1 pt in, so a 2 pt stroke stays inside the frame like
    /// `strokeBorder`) with a triangular tail out of `tail`, all one path. A side tail is centered 25 pt below the top,
    /// or halfway down a shorter card; a top or bottom tail is centered. Corners next to the tail shrink to make room.
    public struct BubbleShape: SwiftUI.Shape {
        public var tail: Edge

        public static let radius: CGFloat = 14
        public static let tailLength: CGFloat = 10
        public static let tailBase: CGFloat = 14
        /// The side tail's center below the card's top, at most.
        public static let tailDrop: CGFloat = 25

        public init(tail: Edge) {
            self.tail = tail
        }

        /// Where a side tail's tip sits below the top of a card `height` tall.
        public static func tailY(cardHeight height: CGFloat) -> CGFloat { min(tailDrop, height / 2) }

        public func path(in frame: CGRect) -> Path {
            let rect = frame.insetBy(dx: 1, dy: 1)
            let half = Self.tailBase / 2
            let length = Self.tailLength - 1
            let tailY = frame.minY + Self.tailY(cardHeight: frame.height)
            // The corners on the tail's edge, so they never overlap its base.
            func corner(_ room: CGFloat) -> CGFloat { max(0, min(Self.radius, room)) }
            var r = (topLeft: Self.radius, topRight: Self.radius, bottomRight: Self.radius, bottomLeft: Self.radius)
            switch tail {
            case .leading:
                r.topLeft = corner(tailY - half - rect.minY)
                r.bottomLeft = corner(rect.maxY - tailY - half)
            case .trailing:
                r.topRight = corner(tailY - half - rect.minY)
                r.bottomRight = corner(rect.maxY - tailY - half)
            case .top:
                r.topLeft = corner(rect.midX - half - rect.minX)
                r.topRight = r.topLeft
            case .bottom:
                r.bottomLeft = corner(rect.midX - half - rect.minX)
                r.bottomRight = r.bottomLeft
            }

            var path = Path()
            path.move(to: CGPoint(x: rect.minX + r.topLeft, y: rect.minY))
            if tail == .top {
                path.addLine(to: CGPoint(x: rect.midX - half, y: rect.minY))
                path.addLine(to: CGPoint(x: rect.midX, y: rect.minY - length))
                path.addLine(to: CGPoint(x: rect.midX + half, y: rect.minY))
            }
            path.addArc(
                tangent1End: CGPoint(x: rect.maxX, y: rect.minY), tangent2End: CGPoint(x: rect.maxX, y: rect.maxY),
                radius: r.topRight)
            if tail == .trailing {
                path.addLine(to: CGPoint(x: rect.maxX, y: tailY - half))
                path.addLine(to: CGPoint(x: rect.maxX + length, y: tailY))
                path.addLine(to: CGPoint(x: rect.maxX, y: tailY + half))
            }
            path.addArc(
                tangent1End: CGPoint(x: rect.maxX, y: rect.maxY), tangent2End: CGPoint(x: rect.minX, y: rect.maxY),
                radius: r.bottomRight)
            if tail == .bottom {
                path.addLine(to: CGPoint(x: rect.midX + half, y: rect.maxY))
                path.addLine(to: CGPoint(x: rect.midX, y: rect.maxY + length))
                path.addLine(to: CGPoint(x: rect.midX - half, y: rect.maxY))
            }
            path.addArc(
                tangent1End: CGPoint(x: rect.minX, y: rect.maxY), tangent2End: CGPoint(x: rect.minX, y: rect.minY),
                radius: r.bottomLeft)
            if tail == .leading {
                path.addLine(to: CGPoint(x: rect.minX, y: tailY + half))
                path.addLine(to: CGPoint(x: rect.minX - length, y: tailY))
                path.addLine(to: CGPoint(x: rect.minX, y: tailY - half))
            }
            path.addArc(
                tangent1End: CGPoint(x: rect.minX, y: rect.minY), tangent2End: CGPoint(x: rect.maxX, y: rect.minY),
                radius: r.topLeft)
            path.closeSubpath()
            return path
        }
    }
#endif
