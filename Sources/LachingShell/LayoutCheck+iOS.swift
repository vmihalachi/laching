#if os(iOS)
    import SwiftUI
    import UIKit

    /// Finds what doesn't fit on an iPhone or iPad screen, the iOS twin of the Mac's check: SwiftUI content taller than
    /// the room it has outside a scroll view (the screen squeezes it, cutting buttons and text off at the bottom), what
    /// is pushed off the screen, SwiftUI text cut short (measured from what accessibility says of it, so the Simulator
    /// needs accessibility on) and UIKit labels cut short with "…" (tab bar items, navigation titles, bar buttons). Draws
    /// nothing and needs the main actor.
    @MainActor
    public enum LayoutCheck {
        /// Everything in `window` that doesn't fit, one line each: `overflows(in:)`, `offScreen(in:)`, then
        /// `truncatedText(in:)` and `truncatedLabels(in:)`.
        public static func problems(in window: UIWindow) -> [String] {
            var cut: [String] = []
            for text in truncatedText(in: window) + truncatedLabels(in: window) where !cut.contains(text) { cut.append(text) }
            return overflows(in: window).map(\.description) + offScreen(in: window).map { "“\($0)” runs off the screen" }
                + cut.map { "“\($0)” is cut short" }
        }

        /// The labels of what VoiceOver would read in `window` whose text, laid out in its own font at its element's
        /// width, needs more lines than the element is tall (`isCut(_:inputLabels:font:in:)`): SwiftUI `Text` cut short
        /// with "…" or clipped. Reads the font from accessibility, so it needs accessibility on, as `offScreen(in:)`
        /// does; measures text only (not a button's title), and skips an element that combines texts in several fonts
        /// or is read as something other than what it draws: SwiftUI doesn't say what an element draws, only what it's
        /// read as, so one that draws a short tag but reads a whole sentence says so with a concise input label
        /// (`accessibilityInputLabels`). Errs towards fitting: a label read differently from how it's drawn
        /// (upper-cased, spaced out) measures shorter. Text shrunk to fit (`minimumScaleFactor`) is measured at the size
        /// it was drawn.
        public static func truncatedText(in window: UIWindow) -> [String] {
            let debug = ProcessInfo.processInfo.environment["LACHING_LAYOUT_DEBUG"] == "1"
            var found: [String] = []
            for item in accessibleItems(in: window) {
                guard let font = item.font, !found.contains(item.label) else { continue }
                let cut = isCut(item.label, inputLabels: item.inputLabels, font: font, in: item.frame.size)
                if debug {
                    let needed = textHeight(of: item.label, font: font, width: item.frame.width)
                    print(
                        "TEXT \(Int(needed))/\(Int(item.frame.height)) w\(Int(item.frame.width)) \(font.fontName) \(font.pointSize) \(item.label) \(item.inputLabels)\(cut ? " CUT" : "")"
                    )
                }
                if cut { found.append(item.label) }
            }
            return found
        }

        /// The labels of what VoiceOver would read in `window` (SwiftUI text and controls included) that lies partly
        /// outside the screen's safe area, under the status bar or the home indicator or past an edge, and not in a
        /// scroll view's content: what a screen that doesn't scroll pushes off when its content needs more room than
        /// it has, whatever its layout. Catches the screens `overflows(in:)` can't measure, such as one built on a
        /// `GeometryReader`. An element inside a scroll view is scrolled to anywhere along the way that view scrolls
        /// (`ScrollArea`), so a lazy stack's rows that report frames past the content's end before it has laid them
        /// out don't count; one pushed past the side of a scroll view that only scrolls up and down does.
        public static func offScreen(in window: UIWindow) -> [String] {
            let safe = window.bounds.inset(by: window.safeAreaInsets).insetBy(dx: -slack, dy: -slack)
            let screen = window.bounds.insetBy(dx: -slack, dy: -slack)
            let scrolled = scrollAreas(in: window)
            var found: [String] = []
            for item in accessibleItems(in: window) where !(item.isUIKit ? screen : safe).contains(item.frame) {
                let center = CGPoint(x: item.frame.midX, y: item.frame.midY)
                // Reached through no scroll view, an element may still lie in one's content.
                let areas = item.scrolls.isEmpty ? scrolled.map { ScrollArea(content: $0.content) } : item.scrolls
                if !areas.contains(where: { $0.contains(center) }), !found.contains(item.label) { found.append(item.label) }
            }
            return found
        }

        /// The SwiftUI screens in `window` (each hosting controller on screen that holds no other: a tab, a sheet, a
        /// cover) whose content is taller than the room inside their safe area. Screens with a scroll view are
        /// skipped: their content is meant to be taller.
        public static func overflows(in window: UIWindow) -> [Overflow] {
            screens(in: window).compactMap { host -> Overflow? in
                let insets = host.view.safeAreaInsets
                let room = host.view.bounds.inset(by: insets).size
                guard let host = host as? HostingMeasurable, room.width > 0, room.height > 0, !scrolls(host.view)
                else { return nil }
                // A hosting controller takes its safe area off the size it's offered and adds it back to the size it
                // answers, so it's offered its whole width (offered the room, a sheet beside the closed iPhone Duo's
                // status bar lost its side inset twice) and its answer loses the insets above and below.
                let width = host.view.bounds.width
                let vertical = insets.top + insets.bottom
                let needed = host.sizeThatFits(in: CGSize(width: width, height: .infinity)).height - vertical
                if ProcessInfo.processInfo.environment["LACHING_LAYOUT_DEBUG"] == "1" {
                    let compressed = host.sizeThatFits(in: CGSize(width: width, height: 0)).height - vertical
                    print("LAYOUT \(type(of: host)) room \(room) needed \(needed) compressed \(compressed)")
                }
                return needed > room.height + slack ? Overflow(needed: needed, available: room.height) : nil
            }
        }

        /// The text of the UIKit labels on screen in `window` that end in "…" because they don't fit their width (one
        /// line) or height (several).
        public static func truncatedLabels(in window: UIWindow) -> [String] {
            var found: [String] = []
            func visit(_ view: UIView) {
                guard !view.isHidden, view.alpha > 0.01 else { return }
                if let label = view as? UILabel, let text = label.text, !text.isEmpty, isTruncated(label) {
                    found.append(text)
                }
                view.subviews.forEach(visit)
            }
            visit(window)
            return found
        }

        /// Whether `label` shows less than its whole text.
        public static func isTruncated(_ label: UILabel) -> Bool {
            let bounds = label.bounds
            guard bounds.width > 0, bounds.height > 0 else { return false }
            if label.numberOfLines == 1 {
                return label.intrinsicContentSize.width > bounds.width + slack && !label.adjustsFontSizeToFitWidth
            }
            let all = label.textRect(
                forBounds: CGRect(x: 0, y: 0, width: bounds.width, height: .greatestFiniteMagnitude), limitedToNumberOfLines: 0)
            return all.height > bounds.height + slack
        }

        /// Whether `view` or one of its subviews scrolls vertically (SwiftUI's `ScrollView`, `List`, `Form` and
        /// `TextEditor` are `UIScrollView`s underneath); one that only scrolls sideways (a row of chips) doesn't count.
        public static func scrolls(_ view: UIView) -> Bool {
            if let scroll = view as? UIScrollView, !view.isHidden, scroll.bounds.height > 0,
                scroll.alwaysBounceVertical || scroll.contentSize.height > scroll.bounds.height + slack
                    || scroll.contentSize.width <= scroll.bounds.width + slack
            {
                return true
            }
            return view.subviews.contains(where: scrolls)
        }

        /// What VoiceOver would read on screen: an element's label, its frame in the window's coordinates, whether it is
        /// one of UIKit's own views (a tab bar's buttons sit partly under the home indicator) rather than SwiftUI
        /// content, for text the font its label is drawn in when it has one throughout and what Voice Control users
        /// say for it, and the scroll views it is in. Buttons, images and charts have no font here: what they draw
        /// isn't their label.
        private struct Item {
            let label: String
            let frame: CGRect
            let isUIKit: Bool
            let font: UIFont?
            let inputLabels: [String]
            let scrolls: [ScrollArea]
        }

        /// The accessibility elements on screen in `window` with a label. Empty unless accessibility is on (the
        /// Simulator's `ApplicationAccessibilityEnabled` default): UIKit and SwiftUI build the tree only then.
        private static func accessibleItems(in window: UIWindow) -> [Item] {
            var found: [Item] = []
            var seen = Set<ObjectIdentifier>()
            func visit(_ object: NSObject, in scrolls: [ScrollArea]) {
                guard seen.insert(ObjectIdentifier(object)).inserted else { return }
                if let view = object as? UIView, view.isHidden || view.alpha < 0.01 { return }
                if object.isAccessibilityElement {
                    let frame = window.screen.coordinateSpace.convert(object.accessibilityFrame, to: window)
                    if let label = object.accessibilityLabel, !label.isEmpty, !frame.isEmpty {
                        let traits = object.accessibilityTraits
                        let isText = traits.contains(.staticText) && !traits.contains(.image)
                        found.append(
                            Item(
                                label: label, frame: frame, isUIKit: object is UIView, font: isText ? font(of: object) : nil,
                                inputLabels: isText ? object.accessibilityUserInputLabels ?? [] : [], scrolls: scrolls))
                    }
                    return
                }
                var scrolls = scrolls
                if let scroll = object as? UIScrollView, scroll.bounds.height > 0 { scrolls.append(area(of: scroll, in: window)) }
                if let elements = object.accessibilityElements as? [NSObject] {
                    for element in elements { visit(element, in: scrolls) }
                } else if let view = object as? UIView {
                    for subview in view.subviews { visit(subview, in: scrolls) }
                }
            }
            visit(window, in: [])
            return found
        }

        /// The font `object`'s label is drawn in, from the attributes accessibility gives it, when one font covers all
        /// of it (an element that combines several texts has several, or none).
        private static func font(of object: NSObject) -> UIFont? {
            guard let label = object.accessibilityAttributedLabel, label.length > 0 else { return nil }
            let all = NSRange(location: 0, length: label.length)
            func whole(_ key: String) -> Any? {
                var range = NSRange()
                let value = label.attribute(NSAttributedString.Key(key), at: 0, longestEffectiveRange: &range, in: all)
                return range == all ? value : nil
            }
            guard let name = whole("UIAccessibilityTokenFontName") as? String,
                let size = whole("UIAccessibilityTokenFontSize") as? NSNumber
            else { return nil }
            return UIFont(name: name, size: CGFloat(size.doubleValue))
        }

        /// Where the content of every scroll view on screen in `window` lies (`area(of:in:)`): what lies there is meant
        /// to be scrolled to.
        private static func scrollAreas(in window: UIWindow) -> [ScrollArea] {
            var found: [ScrollArea] = []
            func visit(_ view: UIView) {
                guard !view.isHidden else { return }
                if let scroll = view as? UIScrollView, scroll.bounds.height > 0 { found.append(area(of: scroll, in: window)) }
                view.subviews.forEach(visit)
            }
            visit(window)
            return found
        }

        /// `scroll`'s content and its own frame in `window`'s coordinates, open-ended along each way it scrolls: up and
        /// down when it bounces that way or its content is taller than it, sideways likewise.
        private static func area(of scroll: UIScrollView, in window: UIWindow) -> ScrollArea {
            let content = CGRect(origin: .zero, size: scroll.contentSize).union(scroll.bounds)
            return ScrollArea(
                content: scroll.convert(content, to: window),
                vertical: scroll.alwaysBounceVertical || scroll.contentSize.height > scroll.bounds.height + slack,
                horizontal: scroll.alwaysBounceHorizontal || scroll.contentSize.width > scroll.bounds.width + slack)
        }

        /// The hosting controllers on screen that hold no other: the screens a user sees, not the tab view or
        /// navigation stack around them.
        private static func screens(in window: UIWindow) -> [UIViewController] {
            var found: [UIViewController] = []
            func visit(_ controller: UIViewController) {
                let shown = controller.isViewLoaded && controller.view.window != nil && !controller.view.isHidden
                let children = controller.children.filter { $0.isViewLoaded && $0.view.window != nil }
                let before = found.count
                children.forEach(visit)
                if let presented = controller.presentedViewController, presented.presentingViewController === controller {
                    visit(presented)
                }
                if shown, found.count == before, controller is HostingMeasurable { found.append(controller) }
            }
            if let root = window.rootViewController { visit(root) }
            return found
        }
    }

    /// SwiftUI hosting, whatever its root view's type.
    @MainActor
    private protocol HostingMeasurable: UIViewController {
        func sizeThatFits(in size: CGSize) -> CGSize
    }

    extension UIHostingController: HostingMeasurable {}
#endif
