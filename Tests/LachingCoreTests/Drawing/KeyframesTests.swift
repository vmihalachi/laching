#if os(macOS)
    import LachingCore
    import XCTest

    final class KeyframesTests: XCTestCase {
        func testHoldsTheEndValuesOutsideTheStops() {
            let stops = [(0.2, 1.0), (0.8, 3.0)]
            XCTAssertEqual(Keyframes.value(0, stops), 1)
            XCTAssertEqual(Keyframes.value(0.2, stops), 1)
            XCTAssertEqual(Keyframes.value(0.8, stops), 3)
            XCTAssertEqual(Keyframes.value(1, stops), 3)
        }

        func testEasesInAndOutBetweenStops() {
            let stops = [(0.0, 0.0), (1.0, 10.0)]
            XCTAssertEqual(Keyframes.value(0.5, stops), 5, accuracy: 1e-9)
            XCTAssertLessThan(Keyframes.value(0.25, stops), 2.5, "slow start")
            XCTAssertGreaterThan(Keyframes.value(0.75, stops), 7.5, "slow end")
            XCTAssertEqual(Keyframes.value(0.25, stops), 1.5625, accuracy: 1e-9)
        }

        func testPicksTheRightSegment() {
            let stops = [(0.0, 0.0), (0.5, 10.0), (1.0, 0.0)]
            XCTAssertEqual(Keyframes.value(0.25, stops), 5, accuracy: 1e-9)
            XCTAssertEqual(Keyframes.value(0.5, stops), 10, accuracy: 1e-9)
            XCTAssertEqual(Keyframes.value(0.75, stops), 5, accuracy: 1e-9)
        }

        func testNoStopsGiveZero() {
            XCTAssertEqual(Keyframes.value(0.5, []), 0)
        }
    }
#endif
