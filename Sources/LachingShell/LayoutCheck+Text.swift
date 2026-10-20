#if os(macOS) || os(iOS)
    import CoreText
    #if os(macOS)
        import AppKit
    #else
        import UIKit
    #endif

    extension LayoutCheck {
        #if os(macOS)
            public typealias PlatformFont = NSFont
        #else
            public typealias PlatformFont = UIFont
        #endif

        /// Whether text that accessibility reads as `label`, drawn in `font` in an element of `size`, is cut short:
        /// laid out at the element's width, it needs more lines than the element is tall. Errs towards fitting, so
        /// these count as fitting: a label its `inputLabels` leave out (an element read as a sentence while it draws a
        /// short tag says so with a concise input label, `accessibilityInputLabels`, the text Voice Control users
        /// see and say), text the font has no glyphs for (Japanese in a Latin font is drawn in a fallback font) and an
        /// element too narrow for a letter and "…" (an icon labeled for VoiceOver, not text). Text shrunk to fit
        /// (`minimumScaleFactor`) draws its line in a smaller font than the one accessibility names, so an element
        /// shorter than a line of `font` is measured in the font at the size it was drawn: a word shrunk to fit its
        /// line fits, one that needed more than its least scale still doesn't.
        public static func isCut(_ label: String, inputLabels: [String] = [], font: PlatformFont, in size: CGSize) -> Bool {
            guard inputLabels.isEmpty || inputLabels.contains(label), covers(font, label) else { return false }
            let least = (String(label.prefix(1)) + "…" as NSString).size(withAttributes: [.font: font]).width
            guard size.width >= least else { return false }
            let line = lineHeight(of: font)
            let drawn = size.height < line * 0.9 ? font.withSize(font.pointSize * size.height / line) : font
            return textHeight(of: label, font: drawn, width: size.width) > size.height + lineHeight(of: drawn) / 2
        }

        /// The height `text` needs in `font`, wrapped at `width`.
        static func textHeight(of text: String, font: PlatformFont, width: CGFloat) -> CGFloat {
            (text as NSString).boundingRect(
                with: CGSize(width: width + slack, height: .greatestFiniteMagnitude),
                options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: font], context: nil
            ).height
        }

        private static func lineHeight(of font: PlatformFont) -> CGFloat {
            #if os(macOS)
                font.ascender - font.descender + font.leading
            #else
                font.lineHeight
            #endif
        }

        /// Whether `font` has a glyph for every character of `text`.
        private static func covers(_ font: PlatformFont, _ text: String) -> Bool {
            let characters = Array(text.utf16)
            var glyphs = [CGGlyph](repeating: 0, count: characters.count)
            return CTFontGetGlyphsForCharacters(font as CTFont, characters, &glyphs, characters.count)
        }
    }
#endif
