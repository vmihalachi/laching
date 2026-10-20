#if os(macOS)
    import AppKit
    import LachingShell
    import SwiftUI
    import XCTest

    @MainActor
    final class LayoutCheckTests: XCTestCase {
        /// A page: a block of the given height, then a button row at the bottom, as a window lays one out.
        private func page(_ height: CGFloat) -> some View {
            VStack(spacing: 0) {
                Color.clear.frame(height: height)
                Spacer(minLength: 16)
                Text("Done").padding(8)
            }
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }

        private func window(_ content: some View, size: CGSize) -> NSWindow {
            let window = NSWindow(
                contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentViewController = NSHostingController(rootView: content)
            window.setContentSize(size)
            window.contentView?.layoutSubtreeIfNeeded()
            addTeardownBlock { window.close() }
            return window
        }

        func testContentThatFitsHasNoOverflow() {
            XCTAssertNil(LayoutCheck.overflow(of: page(200), in: CGSize(width: 300, height: 400)))
            XCTAssertNil(LayoutCheck.overflow(in: window(page(200), size: CGSize(width: 300, height: 400))))
        }

        func testTallerContentReportsWhatItNeeds() throws {
            let overflow = try XCTUnwrap(LayoutCheck.overflow(of: page(400), in: CGSize(width: 300, height: 400)))
            XCTAssertEqual(overflow.available, 400)
            XCTAssertGreaterThan(overflow.needed, 400 + 48)  // the block, the spacer's minimum, the button, the padding
            XCTAssertTrue(overflow.description.hasPrefix("the content needs"))
            XCTAssertNotNil(LayoutCheck.overflow(in: window(page(400), size: CGSize(width: 300, height: 400))))
        }

        func testTextWrapsToTheWidthBeforeItIsMeasured() {
            let text = Text(String(repeating: "Words that wrap onto many lines. ", count: 40))
            XCTAssertGreaterThan(LayoutCheck.neededHeight(of: text, width: 200), LayoutCheck.neededHeight(of: text, width: 600))
            XCTAssertNotNil(LayoutCheck.overflow(of: text, in: CGSize(width: 200, height: 100)))
        }

        func testWindowsThatScrollAreSkipped() {
            let scrolling = ScrollView { page(1000) }
            XCTAssertNil(LayoutCheck.overflow(in: window(scrolling, size: CGSize(width: 300, height: 400))))
        }

        func testWindowsWithoutSwiftUIAreSkipped() {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.borderless], backing: .buffered,
                defer: false)
            window.isReleasedWhenClosed = false
            XCTAssertNil(LayoutCheck.overflow(in: window))
        }

        func testContentRoomLeavesTheTitleBarOut() {
            let room = LayoutCheck.contentRoom(forWindowOfSize: CGSize(width: 400, height: 500))
            XCTAssertEqual(room.width, 400)
            XCTAssertLessThan(room.height, 500)
            XCTAssertGreaterThan(room.height, 440)
        }

        /// iOS's `offScreen(in:)`: a lazy stack's row that reports a frame past the content's end is scrolled to; one
        /// pushed past the side of a stack that only scrolls up and down isn't.
        func testScrollAreasAreOpenAlongTheWayTheyScroll() {
            let content = CGRect(x: 0, y: 96, width: 1032, height: 4548)
            let list = LayoutCheck.ScrollArea(content: content, vertical: true)
            XCTAssertTrue(list.contains(CGPoint(x: 500, y: 4000)))
            XCTAssertTrue(list.contains(CGPoint(x: 500, y: 5838)))
            XCTAssertFalse(list.contains(CGPoint(x: 1100, y: 400)))
            let chips = LayoutCheck.ScrollArea(content: CGRect(x: 0, y: 100, width: 300, height: 40), horizontal: true)
            XCTAssertTrue(chips.contains(CGPoint(x: 900, y: 120)))
            XCTAssertFalse(chips.contains(CGPoint(x: 100, y: 200)))
            let fixed = LayoutCheck.ScrollArea(content: content)
            XCTAssertTrue(fixed.contains(CGPoint(x: 500, y: 4000)))
            XCTAssertFalse(fixed.contains(CGPoint(x: 500, y: 5838)))
        }

        /// iOS's `truncatedText(in:)`: a sentence that needs more lines than its element is tall is cut short, unless
        /// the element's input labels say it draws something else (a short tag read as the whole sentence).
        func testTextIsCutShortUnlessItDrawsSomethingElse() {
            let font = NSFont.monospacedSystemFont(ofSize: 14, weight: .regular)
            let sentence = "Raise the camera so it sees your head too"
            let tag = CGSize(width: 140, height: 24)  // one line
            XCTAssertTrue(LayoutCheck.isCut(sentence, font: font, in: tag))
            XCTAssertTrue(LayoutCheck.isCut(sentence, inputLabels: [sentence], font: font, in: tag))
            XCTAssertFalse(LayoutCheck.isCut(sentence, inputLabels: ["Raise camera"], font: font, in: tag))
            XCTAssertFalse(LayoutCheck.isCut("Raise camera", font: font, in: tag))
            XCTAssertFalse(LayoutCheck.isCut(sentence, font: font, in: CGSize(width: 140, height: 80)))  // wrapped
            XCTAssertFalse(LayoutCheck.isCut(sentence, font: font, in: CGSize(width: 6, height: 24)))  // an icon
            XCTAssertFalse(LayoutCheck.isCut("カメラを上げて、頭も映るようにしてください", font: font, in: tag))  // a fallback font
        }

        /// Text shrunk to fit (`minimumScaleFactor`): one word drawn smaller than its font so it fits its line, its
        /// element shorter than a line of the font, fits; shrunk as far as it may and still too wide, it's cut short.
        func testTextShrunkToFitIsMeasuredAtTheSizeDrawn() {
            let font = NSFont.boldSystemFont(ofSize: 38)
            let word = "SPEEDRUN!"
            let width = (word as NSString).size(withAttributes: [.font: font]).width
            let line = font.ascender - font.descender + font.leading
            XCTAssertFalse(LayoutCheck.isCut(word, font: font, in: CGSize(width: width * 0.72, height: line * 0.72)))
            XCTAssertTrue(LayoutCheck.isCut(word, font: font, in: CGSize(width: width * 0.5, height: line * 0.72)))
            XCTAssertTrue(LayoutCheck.isCut(word, font: font, in: CGSize(width: width * 0.72, height: line)))  // "SPEED…"
        }
    }
#endif
