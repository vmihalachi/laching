#if os(macOS) || os(iOS)
    import CoreGraphics
    import Foundation

    /// Samples SVG path data into dense polylines, one per subpath, for hand-drawn rendering (wobble, dashes,
    /// shadows need points, not curves). Reads `M L H V Q T C S A Z` in absolute and relative form; curves
    /// and arcs are flattened to segments about `step` units long. Results are cached per path string behind
    /// a lock, so any thread may call it. Pure geometry: no views, no drawing.
    public enum SVGPath {
        private enum Token {
            case cmd(Character), num(CGFloat)
        }

        nonisolated(unsafe) private static var cache: [String: [[CGPoint]]] = [:]
        private static let lock = NSLock()

        public static func sample(_ d: String, step: CGFloat = 2) -> [[CGPoint]] {
            let key = step == 2 ? d : "\(step)|\(d)"
            lock.lock()
            let hit = cache[key]
            lock.unlock()
            if let hit { return hit }
            let result = parse(d, step: step)
            lock.lock()
            cache[key] = result
            lock.unlock()
            return result
        }

        /// Drops every cached sampling.
        public static func clearCache() {
            lock.lock()
            cache = [:]
            lock.unlock()
        }

        private static func tokenize(_ d: String) -> [Token] {
            var tokens: [Token] = []
            var buffer = ""
            // 0...6 while inside an arc's parameters, whose 4th and 5th (index 3 and 4) are one-digit flags
            // that may be written without a separator, as in `a1 1 0 00-1 0`; -1 elsewhere.
            var arcParam = -1
            func flush() {
                if let v = Double(buffer) {
                    tokens.append(.num(CGFloat(v)))
                    if arcParam >= 0 { arcParam = (arcParam + 1) % 7 }
                }
                buffer = ""
            }
            for ch in d {
                if ch == "e" || ch == "E", let last = buffer.last, last.isNumber || last == "." {
                    buffer.append(ch)  // exponent
                } else if ch.isLetter {
                    flush()
                    tokens.append(.cmd(ch))
                    arcParam = (ch == "A" || ch == "a") ? 0 : -1
                } else if ch == "-" || ch == "+" {
                    if let last = buffer.last, last == "e" || last == "E" {
                        buffer.append(ch)
                    } else {
                        flush()
                        buffer = ch == "-" ? "-" : ""
                    }
                } else if ch.isNumber || ch == "." {
                    if (arcParam == 3 || arcParam == 4) && ch.isNumber {
                        flush()
                        buffer = String(ch)
                        flush()
                    } else {
                        if ch == ".", buffer.contains("."), !buffer.contains("e"), !buffer.contains("E") { flush() }
                        buffer.append(ch)
                    }
                } else {
                    flush()
                }
            }
            flush()
            return tokens
        }

        private static func parse(_ d: String, step: CGFloat) -> [[CGPoint]] {
            let tokens = tokenize(d)
            var i = 0
            var cmd: Character = "M"
            var subpaths: [[CGPoint]] = []
            var current: [CGPoint] = []
            var pen = CGPoint.zero
            var start = CGPoint.zero
            var lastCubicControl: CGPoint?
            var lastQuadControl: CGPoint?

            func num() -> CGFloat? {
                guard i < tokens.count, case .num(let v) = tokens[i] else { return nil }
                i += 1
                return v
            }
            func point() -> CGPoint? {
                guard let x = num(), let y = num() else { return nil }
                return CGPoint(x: x, y: y)
            }
            /// A coordinate pair as read, made absolute for a lowercase command.
            func absolute(_ p: CGPoint, relative: Bool) -> CGPoint {
                relative ? CGPoint(x: pen.x + p.x, y: pen.y + p.y) : p
            }
            func line(to p: CGPoint) {
                let n = max(1, Int(hypot(p.x - pen.x, p.y - pen.y) / step))
                for k in 1...n {
                    let t = CGFloat(k) / CGFloat(n)
                    current.append(CGPoint(x: pen.x + (p.x - pen.x) * t, y: pen.y + (p.y - pen.y) * t))
                }
                pen = p
            }
            func cubic(_ c1: CGPoint, _ c2: CGPoint, _ p: CGPoint) {
                let approx = hypot(c1.x - pen.x, c1.y - pen.y) + hypot(c2.x - c1.x, c2.y - c1.y) + hypot(p.x - c2.x, p.y - c2.y)
                let n = max(4, Int(approx / step))
                let p0 = pen
                for k in 1...n {
                    let t = CGFloat(k) / CGFloat(n)
                    let u = 1 - t
                    let x = u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p.x
                    let y = u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p.y
                    current.append(CGPoint(x: x, y: y))
                }
                pen = p
                lastCubicControl = c2
            }
            func quadratic(_ c: CGPoint, _ p: CGPoint) {
                // Elevate the quadratic to a cubic.
                let c1 = CGPoint(x: pen.x + 2 / 3 * (c.x - pen.x), y: pen.y + 2 / 3 * (c.y - pen.y))
                let c2 = CGPoint(x: p.x + 2 / 3 * (c.x - p.x), y: p.y + 2 / 3 * (c.y - p.y))
                cubic(c1, c2, p)
                lastCubicControl = nil
                lastQuadControl = c
            }
            /// SVG implementation notes F.6.5: endpoint parameters to center parameterization, then sampled
            /// by angle.
            func arc(_ radii: CGPoint, rotation: CGFloat, largeArc: Bool, sweep: Bool, _ p: CGPoint) {
                guard p != pen else { return }
                var rx = abs(radii.x)
                var ry = abs(radii.y)
                guard rx > 0, ry > 0 else { return line(to: p) }
                let phi = rotation * .pi / 180
                let cosPhi = cos(phi)
                let sinPhi = sin(phi)
                let dx2 = (pen.x - p.x) / 2
                let dy2 = (pen.y - p.y) / 2
                let x1p = cosPhi * dx2 + sinPhi * dy2
                let y1p = -sinPhi * dx2 + cosPhi * dy2
                let lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
                if lambda > 1 {
                    rx *= sqrt(lambda)
                    ry *= sqrt(lambda)
                }
                let numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
                let denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
                let coefficient = (largeArc != sweep ? 1 : -1) * sqrt(max(0, denominator == 0 ? 0 : numerator / denominator))
                let cxp = coefficient * (rx * y1p / ry)
                let cyp = coefficient * -(ry * x1p / rx)
                let cx = cosPhi * cxp - sinPhi * cyp + (pen.x + p.x) / 2
                let cy = sinPhi * cxp + cosPhi * cyp + (pen.y + p.y) / 2
                func angle(_ ux: CGFloat, _ uy: CGFloat, _ vx: CGFloat, _ vy: CGFloat) -> CGFloat {
                    let dot = ux * vx + uy * vy
                    let length = hypot(ux, uy) * hypot(vx, vy)
                    guard length > 0 else { return 0 }
                    let sign: CGFloat = ux * vy - uy * vx < 0 ? -1 : 1
                    return sign * acos(min(1, max(-1, dot / length)))
                }
                let theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
                var delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
                if !sweep, delta > 0 {
                    delta -= 2 * .pi
                } else if sweep, delta < 0 {
                    delta += 2 * .pi
                }
                let n = max(4, Int(abs(delta) * max(rx, ry) / step))
                for k in 1...n {
                    let t = theta1 + delta * CGFloat(k) / CGFloat(n)
                    let x = cosPhi * rx * cos(t) - sinPhi * ry * sin(t) + cx
                    let y = sinPhi * rx * cos(t) + cosPhi * ry * sin(t) + cy
                    current.append(k == n ? p : CGPoint(x: x, y: y))
                }
                pen = p
            }
            /// Skips a parameter list that doesn't parse.
            func skip() {
                i += 1
            }

            while i < tokens.count {
                if case .cmd(let c) = tokens[i] {
                    cmd = c
                    i += 1
                    if c == "Z" || c == "z" {
                        line(to: start)
                        lastCubicControl = nil
                        lastQuadControl = nil
                        continue
                    }
                }
                let relative = cmd.isLowercase
                switch cmd.uppercased() {
                case "M":
                    guard let p = point().map({ absolute($0, relative: relative) }) else {
                        skip()
                        continue
                    }
                    if current.count > 1 { subpaths.append(current) }
                    current = [p]
                    pen = p
                    start = p
                    cmd = relative ? "l" : "L"
                    lastCubicControl = nil
                    lastQuadControl = nil
                case "L":
                    guard let p = point().map({ absolute($0, relative: relative) }) else {
                        skip()
                        continue
                    }
                    line(to: p)
                    lastCubicControl = nil
                    lastQuadControl = nil
                case "H":
                    guard let x = num() else {
                        skip()
                        continue
                    }
                    line(to: CGPoint(x: relative ? pen.x + x : x, y: pen.y))
                    lastCubicControl = nil
                    lastQuadControl = nil
                case "V":
                    guard let y = num() else {
                        skip()
                        continue
                    }
                    line(to: CGPoint(x: pen.x, y: relative ? pen.y + y : y))
                    lastCubicControl = nil
                    lastQuadControl = nil
                case "Q":
                    guard let c = point(), let p = point() else {
                        skip()
                        continue
                    }
                    quadratic(absolute(c, relative: relative), absolute(p, relative: relative))
                case "T":
                    guard let p = point() else {
                        skip()
                        continue
                    }
                    let c = lastQuadControl.map { CGPoint(x: 2 * pen.x - $0.x, y: 2 * pen.y - $0.y) } ?? pen
                    quadratic(c, absolute(p, relative: relative))
                case "C":
                    guard let c1 = point(), let c2 = point(), let p = point() else {
                        skip()
                        continue
                    }
                    cubic(absolute(c1, relative: relative), absolute(c2, relative: relative), absolute(p, relative: relative))
                    lastQuadControl = nil
                case "S":
                    guard let c2 = point(), let p = point() else {
                        skip()
                        continue
                    }
                    let c1 = lastCubicControl.map { CGPoint(x: 2 * pen.x - $0.x, y: 2 * pen.y - $0.y) } ?? pen
                    cubic(c1, absolute(c2, relative: relative), absolute(p, relative: relative))
                    lastQuadControl = nil
                case "A":
                    guard let radii = point(), let rotation = num(), let largeArc = num(), let sweep = num(),
                        let p = point()
                    else {
                        skip()
                        continue
                    }
                    arc(radii, rotation: rotation, largeArc: largeArc != 0, sweep: sweep != 0, absolute(p, relative: relative))
                    lastCubicControl = nil
                    lastQuadControl = nil
                default:
                    skip()
                }
            }
            if current.count > 1 { subpaths.append(current) }
            return subpaths
        }
    }
#endif
