#if os(macOS)
    import LachingCore
    import XCTest

    final class SVGPathTests: XCTestCase {
        private func assertEqual(_ a: CGPoint, _ b: CGPoint, accuracy: CGFloat = 0.01, line: UInt = #line) {
            XCTAssertEqual(a.x, b.x, accuracy: accuracy, "x", line: line)
            XCTAssertEqual(a.y, b.y, accuracy: accuracy, "y", line: line)
        }

        private func assertEqual(_ a: [[CGPoint]], _ b: [[CGPoint]], line: UInt = #line) {
            XCTAssertEqual(a.count, b.count, "subpaths", line: line)
            for (pa, pb) in zip(a, b) {
                XCTAssertEqual(pa.count, pb.count, "points", line: line)
                for (x, y) in zip(pa, pb) { assertEqual(x, y, line: line) }
            }
        }

        func testAbsoluteLinesAreSampledEveryStep() {
            let paths = SVGPath.sample("M 0 0 L 10 0 L 10 10", step: 2)
            XCTAssertEqual(paths.count, 1)
            let pts = paths[0]
            XCTAssertEqual(pts.count, 11, "start + 5 + 5")
            assertEqual(pts[0], .zero)
            assertEqual(pts[5], CGPoint(x: 10, y: 0))
            assertEqual(pts[10], CGPoint(x: 10, y: 10))
        }

        func testEachMoveStartsASubpath() {
            let paths = SVGPath.sample("M0,0 L10,0 M0,10 L10,10", step: 5)
            XCTAssertEqual(paths.count, 2)
            assertEqual(paths[1][0], CGPoint(x: 0, y: 10))
            assertEqual(paths[1].last!, CGPoint(x: 10, y: 10))
        }

        func testRelativeCommandsMatchAbsolute() {
            let absolute = SVGPath.sample("M 10 10 L 20 10 L 20 20 C 20 30 10 30 10 20 Q 5 15 10 10 Z", step: 1)
            let relative = SVGPath.sample("m 10 10 l 10 0 l 0 10 c 0 10 -10 10 -10 0 q -5 -5 0 -10 z", step: 1)
            assertEqual(absolute, relative)
            assertEqual(absolute[0].last!, CGPoint(x: 10, y: 10))
        }

        func testImplicitRepeatsAfterMoveAreLines() {
            let absolute = SVGPath.sample("M 0 0 10 0 10 10", step: 5)
            let relative = SVGPath.sample("m 0 0 10 0 0 10", step: 5)
            assertEqual(absolute, relative)
            assertEqual(absolute[0].last!, CGPoint(x: 10, y: 10))
        }

        func testHorizontalAndVerticalLines() {
            let pts = SVGPath.sample("M 0 0 H 10 V 10 h -10 v -10", step: 5)[0]
            XCTAssertEqual(pts.count, 9)
            assertEqual(pts[2], CGPoint(x: 10, y: 0))
            assertEqual(pts[4], CGPoint(x: 10, y: 10))
            assertEqual(pts[6], CGPoint(x: 0, y: 10))
            assertEqual(pts[8], .zero)
        }

        func testSmoothCurvesReflectTheLastControlPoint() {
            // S after C reflects C's second control point; written out as a C it must sample the same.
            let smooth = SVGPath.sample("M 0 0 C 0 10 10 10 10 0 S 20 -10 20 0", step: 1)
            let explicit = SVGPath.sample("M 0 0 C 0 10 10 10 10 0 C 10 -10 20 -10 20 0", step: 1)
            assertEqual(smooth, explicit)
            let quadSmooth = SVGPath.sample("M 0 0 Q 5 10 10 0 T 20 0", step: 1)
            let quadExplicit = SVGPath.sample("M 0 0 Q 5 10 10 0 Q 15 -10 20 0", step: 1)
            assertEqual(quadSmooth, quadExplicit)
        }

        func testQuadraticPeaksHalfway() {
            let pts = SVGPath.sample("M 0 0 Q 5 10 10 0", step: 0.5)[0]
            assertEqual(pts.last!, CGPoint(x: 10, y: 0))
            XCTAssertEqual(pts.map(\.y).max()!, 5, accuracy: 0.05)
        }

        func testArcsStayOnTheCircleAndHonorTheSweepFlag() {
            // Half circles of radius 10 from (0,0) to (20,0), centered on (10,0).
            let sweep = SVGPath.sample("M 0 0 A 10 10 0 0 1 20 0", step: 1)[0]
            let counter = SVGPath.sample("M 0 0 A 10 10 0 0 0 20 0", step: 1)[0]
            for pts in [sweep, counter] {
                assertEqual(pts.last!, CGPoint(x: 20, y: 0))
                XCTAssertGreaterThan(pts.count, 20)
                for p in pts { XCTAssertEqual(hypot(p.x - 10, p.y), 10, accuracy: 0.01) }
            }
            // Positive-angle direction goes from +x toward +y, so sweep=1 passes through y = -10 here
            // (clockwise on a y-down screen) and sweep=0 through y = +10.
            XCTAssertLessThan(sweep.map(\.y).min()!, -9.9)
            XCTAssertGreaterThan(counter.map(\.y).max()!, 9.9)
        }

        func testLargeArcFlagPicksTheLongWayRound() {
            let short = SVGPath.sample("M 0 0 A 10 10 0 0 1 10 10", step: 1)[0]
            let long = SVGPath.sample("M 0 0 A 10 10 0 1 1 10 10", step: 1)[0]
            XCTAssertGreaterThan(long.count, short.count * 2)
            assertEqual(short.last!, CGPoint(x: 10, y: 10))
            assertEqual(long.last!, CGPoint(x: 10, y: 10))
        }

        func testRelativeArcsAndCompactFlags() {
            let spaced = SVGPath.sample("M 1 0 a 1 1 0 0 0 -1 0", step: 0.1)
            let compact = SVGPath.sample("M1 0a1 1 0 00-1 0", step: 0.1)
            assertEqual(spaced, compact)
            assertEqual(spaced[0].last!, .zero)
        }

        func testArcWithZeroRadiusIsALine() {
            let pts = SVGPath.sample("M 0 0 A 0 0 0 0 1 10 0", step: 5)[0]
            XCTAssertEqual(pts.count, 3)
            assertEqual(pts.last!, CGPoint(x: 10, y: 0))
        }

        func testScientificNotationAndPackedDecimals() {
            let pts = SVGPath.sample("M 0 0 L 1e1 0 L 10 .5.5", step: 100)[0]
            assertEqual(pts[1], CGPoint(x: 10, y: 0))
            assertEqual(pts[2], CGPoint(x: 10, y: 0.5))
            XCTAssertEqual(pts.count, 3, "The trailing .5 starts a new number and leaves an incomplete pair, which is skipped")
        }

        func testCacheReturnsTheSameSampling() {
            let d = "M 0 0 C 10 20 30 -20 40 0 A 5 5 0 1 0 50 0 Z"
            let first = SVGPath.sample(d)
            let second = SVGPath.sample(d)
            XCTAssertEqual(first, second)
            SVGPath.clearCache()
            XCTAssertEqual(SVGPath.sample(d), first)
            XCTAssertNotEqual(SVGPath.sample(d, step: 0.5)[0].count, first[0].count, "The step is part of the key")
        }
    }
#endif
