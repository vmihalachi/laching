#if os(macOS) || os(iOS)
    import CoreGraphics
    import Foundation

    /// A smooth, stable hand-drawn wobble for sampled polylines, the way an SVG `feTurbulence` +
    /// `feDisplacementMap` filter roughens strokes. Pure arithmetic; the same seed always gives the same
    /// line, so animated figures keep their character instead of shimmering.
    public enum Wobble {
        /// Smooth pseudo-noise in roughly -1...1, stable for a given seed.
        public static func noise(_ s: CGFloat, seed: Int) -> CGFloat {
            let k = CGFloat(seed)
            return sin(s * 0.19 + k * 1.7) * 0.55 + sin(s * 0.47 + k * 2.9) * 0.3 + sin(s * 1.03 + k * 0.7) * 0.15
        }

        /// Nudges each point by `amount` along the polyline's arc length, so the wobble travels with the
        /// line when it moves.
        public static func apply(_ pts: [CGPoint], seed: Int, amount: CGFloat) -> [CGPoint] {
            guard amount > 0, pts.count > 1 else { return pts }
            var s: CGFloat = 0
            var out: [CGPoint] = []
            out.reserveCapacity(pts.count)
            for (i, p) in pts.enumerated() {
                if i > 0 { s += hypot(p.x - pts[i - 1].x, p.y - pts[i - 1].y) }
                out.append(CGPoint(x: p.x + noise(s, seed: seed) * amount, y: p.y + noise(s, seed: seed + 17) * amount))
            }
            return out
        }
    }
#endif
