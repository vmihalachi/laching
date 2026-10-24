#if os(macOS)
    import SwiftUI

    /// The narrowest whole-point width that wraps words into no more lines than the offered width does (CSS's
    /// `text-wrap: balance`, which SwiftUI lacks), so a bubble's last line doesn't hold a lone word when evener lines
    /// fit: "Leaf by leaf, bit by bit, the day / goes." becomes "Leaf by leaf, bit by / bit, the day goes.". `height`
    /// measures the words at a width; a height that never shrinks as the width grows is all it assumes.
    public enum BalancedWrap {
        public static func width(within width: CGFloat, height: (CGFloat) -> CGFloat) -> CGFloat {
            let lines = height(width)
            var (fits, tooNarrow) = (width.rounded(.up), CGFloat(0))
            while fits - tooNarrow > 1 {
                let middle = ((fits + tooNarrow) / 2).rounded(.down)
                if height(middle) <= lines { fits = middle } else { tooNarrow = middle }
            }
            return min(width, fits)
        }
    }

    /// Lays its one view (a `Text`) out at `BalancedWrap`'s width: as many lines as at the width offered, evened out.
    struct BalancedLines: Layout {
        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            guard let text = subviews.first else { return .zero }
            let natural = text.sizeThatFits(ProposedViewSize(width: proposal.width, height: nil))
            guard let offered = proposal.width, offered.isFinite else { return natural }
            // Lines counted at the width offered, not at the words' own width: a hair under the offer, Text can wrap
            // once more.
            let width = BalancedWrap.width(within: offered) {
                text.sizeThatFits(ProposedViewSize(width: $0, height: nil)).height
            }
            return CGSize(width: width, height: natural.height)
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: nil))
        }
    }
#endif
