#if os(macOS) || os(iOS)
    import Foundation

    /// Eased keyframes, matching CSS `ease-in-out` timelines closely enough for doodles. Pure arithmetic.
    public enum Keyframes {
        /// `stops` are (progress 0...1, value) pairs in ascending order. Before the first stop the first
        /// value holds, after the last the last; in between, smoothstep between the neighbors.
        public static func value(_ t: Double, _ stops: [(Double, Double)]) -> Double {
            guard let first = stops.first, let last = stops.last else { return 0 }
            if t <= first.0 { return first.1 }
            if t >= last.0 { return last.1 }
            for k in 1..<stops.count where t <= stops[k].0 {
                let a = stops[k - 1]
                let b = stops[k]
                let x = (t - a.0) / max(b.0 - a.0, 0.0001)
                let eased = x * x * (3 - 2 * x)
                return a.1 + (b.1 - a.1) * eased
            }
            return last.1
        }
    }
#endif
