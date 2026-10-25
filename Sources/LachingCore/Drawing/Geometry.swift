#if os(macOS) || os(iOS)
    import CoreGraphics
    import Foundation

    extension CGAffineTransform {
        /// A rotation by `degrees` about `point`, for turning a limb or a doodle in view-box coordinates.
        public static func rotation(degrees: Double, about point: CGPoint) -> CGAffineTransform {
            CGAffineTransform(translationX: point.x, y: point.y)
                .rotated(by: degrees * .pi / 180)
                .translatedBy(x: -point.x, y: -point.y)
        }
    }

    /// Sampled polylines for a circle, matching `SVGPath.sample`'s output shape so wobble and dashes apply
    /// the same way. Starts at twelve o'clock and closes on the first point. Pure geometry.
    public enum Polyline {
        public static func circle(center: CGPoint, radius: CGFloat, minimumPoints: Int = 24) -> [CGPoint] {
            let n = max(minimumPoints, Int(radius * 2.4))
            return (0...n).map { i -> CGPoint in
                let a = Double(i) / Double(n) * 2 * .pi - .pi / 2
                return CGPoint(x: center.x + radius * cos(a), y: center.y + radius * sin(a))
            }
        }
    }
#endif
