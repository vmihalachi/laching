#if os(macOS)
    import LachingCore
    import XCTest

    final class GeometryTests: XCTestCase {
        private let line = (0...20).map { CGPoint(x: CGFloat($0), y: 0) }

        func testWobbleIsStableForASeedAndOffForZeroAmount() {
            XCTAssertEqual(Wobble.apply(line, seed: 3, amount: 0), line)
            let a = Wobble.apply(line, seed: 3, amount: 1)
            let b = Wobble.apply(line, seed: 3, amount: 1)
            XCTAssertEqual(a, b)
            XCTAssertNotEqual(a, line)
            XCTAssertNotEqual(Wobble.apply(line, seed: 4, amount: 1), a)
            XCTAssertEqual(a.count, line.count)
        }

        func testWobbleStaysWithinItsAmount() {
            for (original, moved) in zip(line, Wobble.apply(line, seed: 9, amount: 0.5)) {
                XCTAssertLessThanOrEqual(abs(moved.x - original.x), 0.5)
                XCTAssertLessThanOrEqual(abs(moved.y - original.y), 0.5)
            }
            for s in stride(from: 0, through: 200, by: 7) {
                XCTAssertLessThanOrEqual(abs(Wobble.noise(CGFloat(s), seed: 1)), 1)
            }
        }

        func testRotationAboutAPoint() {
            let turned = CGPoint(x: 2, y: 1).applying(.rotation(degrees: 90, about: CGPoint(x: 1, y: 1)))
            XCTAssertEqual(turned.x, 1, accuracy: 1e-9)
            XCTAssertEqual(turned.y, 2, accuracy: 1e-9)
            let still = CGPoint(x: 1, y: 1).applying(.rotation(degrees: 45, about: CGPoint(x: 1, y: 1)))
            XCTAssertEqual(still.x, 1, accuracy: 1e-9)
            XCTAssertEqual(still.y, 1, accuracy: 1e-9)
        }

        func testCircleClosesAndStartsAtTwelve() {
            let pts = Polyline.circle(center: CGPoint(x: 5, y: 5), radius: 2)
            XCTAssertEqual(pts.count, 25)
            XCTAssertEqual(pts.first!.x, 5, accuracy: 1e-9)
            XCTAssertEqual(pts.first!.y, 3, accuracy: 1e-9)
            XCTAssertEqual(pts.first!.x, pts.last!.x, accuracy: 1e-9)
            XCTAssertEqual(pts.first!.y, pts.last!.y, accuracy: 1e-9)
            for p in pts { XCTAssertEqual(hypot(p.x - 5, p.y - 5), 2, accuracy: 1e-9) }
        }
    }
#endif
