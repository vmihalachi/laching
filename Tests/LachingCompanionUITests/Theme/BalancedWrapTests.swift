#if os(macOS)
    import LachingCompanionUI
    import XCTest

    final class BalancedWrapTests: XCTestCase {
        /// Greedy wrapping of words in a monospaced face, 10 points a character, 20 a line.
        private func height(_ text: String) -> (CGFloat) -> CGFloat {
            let words = text.split(separator: " ").map { CGFloat($0.count) * 10 }
            return { width in
                var (lines, line) = (CGFloat(1), CGFloat(0))
                for word in words {
                    if line > 0 && line + 10 + word > width {
                        (lines, line) = (lines + 1, word)
                    } else {
                        line += line > 0 ? 10 + word : word
                    }
                }
                return lines * 20
            }
        }

        func testTwoLinesMeetInTheMiddle() {
            // At 270: "one two three four five six" over "seven". Balanced: "one two three four" over "five six seven".
            let measure = height("one two three four five six seven")
            XCTAssertEqual(BalancedWrap.width(within: 270, height: measure), 180)
            XCTAssertEqual(measure(180), measure(270))
            XCTAssertGreaterThan(measure(179), measure(270))
        }

        func testEvenLinesStayAsWide() {
            // At 150: "Present! Sunny," / "reporting for" / "duty."; any narrower takes a fourth line.
            XCTAssertEqual(BalancedWrap.width(within: 150, height: height("Present! Sunny, reporting for duty.")), 150)
        }

        func testOneLineHugsItsWords() {
            XCTAssertEqual(BalancedWrap.width(within: 200, height: height("Hello there!")), 120)
        }
    }
#endif
