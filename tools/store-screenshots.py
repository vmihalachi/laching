#!/usr/bin/env python3
"""Composes store screenshots from the captures (`screenshots.store` in companion.json): the Mac App Store's by default,
Google Play's or the Microsoft Store's with --canvas.

    python3 laching/tools/store-screenshots.py [--canvas mac|android-phone|android-tablet|microsoft] [--lang LANG|all]…
        [--out DIR] [captures-dir]

Each language's images land in <store.out>/<language> (<store.out>/<canvas>/<language> for an Android canvas; --out
DIR for DIR/<language>), named <n>-<shot>-<appearance>.png in upload order: upload them from there. --lang (repeatable,
default the source language; `all` for every language of the captions) picks the languages. The captures: the folder
given (one language's), else for the Mac the committed captures in screenshots.out for the source language (run
screenshots.sh first if the windows changed) and, for any other language, the windows its captions need, captured in
that language first (`screenshots.sh --only … --front` into build/store-captures/apple/<language>); for an Android
canvas, screenshots.out. Needs Pillow (pip3 install pillow).

Each image is one capture on the paper of its appearance with a headline (`store.font`) and a short note
(`store.noteFont`, else the same face) in the app's colors (`store.palette`: light/dark → paper, ink, accent,
accentText). Captions live in `store.captions`, one list per language in upload order: `[{"shot": capture,
"appearance": "light"|"dark", "headline": [lines], "note": text}, …]`; keep them to what ships and free of health or
productivity claims. An entry's `windows` object replaces its fields (shot, appearance, headline, note) on the
`microsoft` canvas, its `android` object on the Android ones (other objects, e.g. an app's own `ios`, are left alone).
`store.style` is `rule` (the default: a short accent rule between headline and note, the window rounded with a soft
shadow) or `zine` (a copy of the headline offset in the accent behind it, as a misregistered print, and the window
framed in ink over a hard offset shadow, `store.palette`'s `shadow`, else the ink at 16% on the paper; a larger note,
for a handwritten face). `store.cjkFonts` (language → font file) sets a language's headline and note in its own face
(the display faces seldom have CJK); Japanese and Chinese lines break between characters, not before closing or after
opening punctuation, nor inside a katakana or Latin word, and after a comma, else (Japanese) after the particle ending
a phrase, when that takes no more lines; Korean wraps at its spaces.

Canvases: `mac` is landscape, the window on the right and the text beside it, drawn for 2880 × 1800 and scaled to
`store.size`; `android-phone` (1080 × 1920, 9:16) stacks the caption above the screen, as the iPhone images do, every
shot of a language reserving its tallest caption's height so the screens line up; `android-tablet` (2560 × 1600, a
10-inch tablet) is the landscape layout. `store.canvases` overrides an Android canvas's size; Google Play takes each
side between 320 and 3840 px and no side more than twice the other, which every Android image is checked against.

`microsoft` needs no captions or fonts (Partner Center shows each caption under its image): each Windows capture
named in `store.microsoft.shots` (file names without .png, upload order, one per line of ms.captions) is centered at
its own size, never scaled so its text stays sharp, on a 3840 × 2160 backdrop with a soft shadow. The backdrop is
`store.microsoft.backdrop` (light/dark → color, by the capture's -light or -dark), else the paper of `store.palette`;
`store.microsoft.shadow` ({offset, light, dark}) swaps the soft shadow for a hard one, a block of that color offset by
`offset` px right and down.
The images go to <store.dir>/<store.microsoft.screenshots, microsoft/screenshots by default>/<language> (--out DIR:
DIR/<language>), named <n>-<shot>.png, where import.py takes them; the folder's old PNGs go first. --lang all is every
language of the app. The captures: the folder given, else windows.screenshots for the source language and, for any
other, the windows the shots need captured in that language first (`screenshots.ps1 -Only … -Front -Language` into
build/store-captures/microsoft/<language>; from a Mac through `windows/vm.sh screenshots`). A capture larger than the
canvas, or one flat color below its top quarter (a window captured before it drew), stops the run before anything is
written.

`store.microsoft.captioned` lays the `microsoft` images out as the Mac's instead, for a listing that looks like the Mac
App Store's: on the paper of `store.palette`, the window on the right, scaled to fit and enlarged at most 1.5×, the
caption beside it in `store.style` and fonts (captions and fonts needed; backdrop and shadow don't apply). The
language's captions, after their `windows` objects, go one to one with `store.microsoft.shots` (each entry's shot and
appearance its name), and a language without captions, or with captions that don't match, stops the run before
anything is captured; a capture larger than the canvas is then fine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402

ANDROID_CANVASES = {"android-phone": (1080, 1920), "android-tablet": (2560, 1600)}
# Partner Center takes desktop screenshots from 1366 × 768 up to this.
MICROSOFT_CANVAS = (3840, 2160)
CANVASES = ("mac", *ANDROID_CANVASES, "microsoft")
# A capture's appearance, the end of its file name.
APPEARANCE = re.compile(r"-(light|dark)$")
# Google Play's screenshot limits: each side, and the longer side over the shorter.
PLAY_SIDE = (320, 3840)
PLAY_RATIO = 2
DEFAULT_PALETTE = {
    "light": {"paper": "#FFFFFF", "ink": "#1D1D1F", "accent": "#0A84FF", "accentText": "#0060DF"},
    "dark": {"paper": "#1C1C1E", "ink": "#F5F5F7", "accent": "#0A84FF", "accentText": "#64B5FF"},
}
# Captures are at 2x; enlarging past this gets soft. Windows captures are at the display's scale, so the Microsoft
# Store's window is enlarged less.
MAX_SCALE = 1.8
MICROSOFT_MAX_SCALE = 1.5
# The headline styles (store.style), their numbers for a 2880 × 1800 landscape canvas and a 1080-wide portrait one.
# `rule`: a short accent rule between headline and note, the window with rounded corners and a soft shadow. `zine`: a
# copy of the headline offset in the accent behind it (misregistered print), the window framed in ink with a hard offset
# shadow, a cut-out. `gap` clears the headline's descenders (a share of its size), `after` is the space before the
# note, `cjkNote` the note's size in a CJK face (store.cjkFonts) against the note face's.
STYLES = {
    "rule": {
        "landscape": {"window": (1440, 1360), "right": 200, "left": 200, "gutter": 140, "headline": 168, "step": 4,
                      "leading": 1.12, "gap": 0.2, "after": 56, "note": 76, "noteLeading": 100},
        "portrait": {"margin": 72, "top": 96, "bottom": 72, "headline": 72, "step": 2, "leading": 1.12, "gap": 0.2,
                     "after": 32, "note": 40, "noteLeading": 52, "screen": 48},
        "cjkNote": 1.0,
    },
    "zine": {
        "landscape": {"window": (1480, 1400), "right": 160, "left": 170, "gutter": 120, "headline": 150, "step": 5,
                      "leading": 1.2, "gap": 0, "after": 40, "note": 120, "noteLeading": 120},
        "portrait": {"margin": 72, "top": 96, "bottom": 72, "headline": 80, "step": 2, "leading": 1.2, "gap": 0,
                     "after": 24, "note": 60, "noteLeading": 60, "screen": 56},
        "cjkNote": 0.7,
    },
}
# The caption entry's object that replaces its fields on a canvas (an app's own composer may read others, e.g. ios).
PLATFORMS = {"android-phone": "android", "android-tablet": "android", "microsoft": "windows"}


class Settings:
    """One canvas's composition for one language: its size, colors, style and fonts from companion.json, and where the
    captures are read from and the images written to."""

    def __init__(self, config: companion.Config, canvas: str, captures: Path, out: Path, language: str):
        store = config.require("screenshots.store")
        self.canvas, self.captures, self.out, self.language = canvas, captures, out, language
        if canvas == "mac":
            self.width, self.height = (int(v) for v in store.get("size", [2880, 1800]))
        elif canvas == "microsoft":
            self.width, self.height = MICROSOFT_CANVAS
        else:
            self.width, self.height = (int(v) for v in store.get("canvases", {}).get(canvas, ANDROID_CANVASES[canvas]))
        self.portrait = canvas == "android-phone"
        self.max_scale = MICROSOFT_MAX_SCALE if canvas == "microsoft" else MAX_SCALE
        # The layouts' numbers are for a 2880-wide (landscape) or 1080-wide (portrait) canvas.
        self.unit = self.width / (1080 if self.portrait else 2880)
        self.palette = {mode: {**DEFAULT_PALETTE[mode], **store.get("palette", {}).get(mode, {})} for mode in DEFAULT_PALETTE}
        self.style = store.get("style", "rule")
        self.zine = self.style == "zine"
        self.metrics = STYLES[self.style]["portrait" if self.portrait else "landscape"]
        # The display faces seldom have CJK: those languages set the headline and the note in their own face.
        cjk = config.path(f"screenshots.store.cjkFonts.{language}")
        self.headline_font = cjk or config.require_path("screenshots.store.font")
        self.note_font = cjk or config.path("screenshots.store.noteFont") or self.headline_font
        self.note_size = self.metrics["note"] * (STYLES[self.style]["cjkNote"] if cjk else 1)

    def render(self, captions: list[dict], root: Path) -> None:
        """Writes the language's images, printing each one's path; exits when Google Play would refuse one."""
        self.out.mkdir(parents=True, exist_ok=True)
        captions = for_platform(captions, self.canvas)
        if self.portrait:
            measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            text_height = max(self.portrait_text(measure, caption)[-1] for caption in captions)
        for i, caption in enumerate(captions, 1):
            path = self.out / f"{i}-{caption['shot']}-{caption.get('appearance', 'light')}.png"
            image = self.compose_portrait(caption, text_height) if self.portrait else self.compose(caption)
            if self.canvas != "mac" and (problem := play_problem(image)):
                sys.exit(f"store-screenshots: {path.name} {problem} (store.canvases)")
            image.save(path, optimize=True)
            print(companion.rel(path, root))

    def px(self, value: float) -> int:
        return round(value * self.unit)

    def shot(self, caption) -> Image.Image:
        image = Image.open(self.captures / f"{caption['shot']}-{caption.get('appearance', 'light')}.png")
        # Windows captures keep their alpha, pasted over the frame; the Mac's and Android's are flattened as they were.
        return image.convert("RGBA" if self.canvas == "microsoft" else "RGB")

    def frame(self, canvas, draw, shot, x, y, colors, dark, radius, lift, blur):
        """The window at (x, y): a cut-out framed in ink over a hard offset shadow (zine), else rounded with a soft
        shadow `lift` px below it."""
        mask = shot if shot.mode == "RGBA" else None
        if self.zine:
            border = self.px(4)
            shadow = colors.get("shadow") or mix(colors["paper"], colors["ink"], 0.16)
            draw.rectangle((x + self.px(18), y + self.px(22), x + self.px(18) + shot.width + 2 * border - 1,
                            y + self.px(22) + shot.height + 2 * border - 1), fill=shadow)
            draw.rectangle((x - border, y - border, x + shot.width + border - 1, y + shot.height + border - 1), fill=colors["ink"])
            canvas.paste(shot, (x, y), mask)
            return
        layer = Image.new("L", (self.width, self.height), 0)
        ImageDraw.Draw(layer).rounded_rectangle((x, y + lift, x + shot.width, y + shot.height + lift), radius,
                                                fill=90 if dark else 60)
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        canvas.paste(Image.new("RGB", (self.width, self.height), "#000000"), (0, 0), layer)
        corners = rounded(shot, radius)
        if mask:
            corners = ImageChops.multiply(corners, shot.getchannel("A"))
        canvas.paste(shot, (x, y), corners)
        draw.rounded_rectangle((x, y, x + shot.width - 1, y + shot.height - 1), radius, outline=colors["ink"] + "33", width=2)

    def headline(self, draw, line, font, position, colors):
        """One headline line at `position`, over its accent copy offset right and down in the zine style."""
        if self.zine:
            start = self.metrics["headline"]
            draw.text((position[0] + self.px(start * 0.06), position[1] + self.px(start * 0.047)), line, font=font,
                      fill=colors["accent"])
        draw.text(position, line, font=font, fill=colors["ink"])

    def fitted(self, draw, lines, column):
        """The headline's font, shrunk until its widest line fits the column."""
        size = self.px(self.metrics["headline"])
        while True:
            display = ImageFont.truetype(str(self.headline_font), size)
            if max(draw.textlength(line, font=display) for line in lines) <= column:
                return display, size
            size -= self.metrics["step"]

    def compose(self, caption):
        colors = self.palette[caption.get("appearance", "light")]
        dark = caption.get("appearance") == "dark"
        canvas = Image.new("RGB", (self.width, self.height), colors["paper"])
        draw = ImageDraw.Draw(canvas)
        m = self.metrics

        # The window on the right, filling the same share of the canvas's height whatever its shape.
        shot = self.shot(caption)
        tall, wide = m["window"]
        scale = min(min(self.px(tall), round(tall / 1800 * self.height)) / shot.height, self.px(wide) / shot.width,
                    self.max_scale)
        shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.LANCZOS)
        x, y = self.width - shot.width - self.px(m["right"]), (self.height - shot.height) // 2
        self.frame(canvas, draw, shot, x, y, colors, dark, self.px(36), self.px(24), self.px(40))

        # Headline and note in the left column, centered on the window. Each line is shifted by its left side
        # bearing so the ink edges line up.
        left, column = self.px(m["left"]), x - self.px(m["left"]) - self.px(m["gutter"])
        display, size = self.fitted(draw, caption["headline"], column)
        note_font = ImageFont.truetype(str(self.note_font), self.px(self.note_size))
        leading, note_leading = round(size * m["leading"]), self.px(m["noteLeading"])
        note_lines = wrap(draw, caption["note"], note_font, column, self.language)
        gap = round(size * m["gap"])
        block = len(caption["headline"]) * leading + gap + self.px(m["after"]) + len(note_lines) * note_leading
        ty = y + (shot.height - block) // 2 if self.zine else (self.height - block) // 2
        for line in caption["headline"]:
            self.headline(draw, line, display, (left - display.getbbox(line)[0], ty), colors)
            ty += leading
        ty += gap
        if not self.zine:
            # A short accent rule between headline and note.
            draw.rounded_rectangle((left, ty + self.px(8), left + self.px(160), ty + self.px(26)), self.px(9),
                                   fill=colors["accent"])
        ty += self.px(m["after"])
        for line in note_lines:
            draw.text((left - note_font.getbbox(line)[0], ty), line, font=note_font, fill=colors["accentText"])
            ty += note_leading
        return canvas

    def portrait_text(self, draw, caption):
        """The headline's font (shrunk until its widest line fits the column), the wrapped note and the block's height."""
        m = self.metrics
        column = self.width - 2 * self.px(m["margin"])
        display, size = self.fitted(draw, caption["headline"], column)
        note_font = ImageFont.truetype(str(self.note_font), self.px(self.note_size))
        note_lines = wrap(draw, caption["note"], note_font, column, self.language)
        leading, note_leading = round(size * m["leading"]), self.px(m["noteLeading"])
        gap = round(size * m["gap"])
        height = len(caption["headline"]) * leading + gap + self.px(m["after"]) + len(note_lines) * note_leading
        return display, leading, note_font, note_lines, note_leading, gap, height

    def compose_portrait(self, caption, text_height):
        """A phone canvas: the headline and note centered at the top, the screen below them, scaled to what is left."""
        colors = self.palette[caption.get("appearance", "light")]
        canvas = Image.new("RGB", (self.width, self.height), colors["paper"])
        draw = ImageDraw.Draw(canvas)
        m = self.metrics
        display, leading, note_font, note_lines, note_leading, gap, height = self.portrait_text(draw, caption)

        def centered_x(text, font):
            left, _, right, _ = font.getbbox(text)
            return (self.width - (right - left)) // 2 - left

        top = self.px(m["top"])
        ty = top + (text_height - height) // 2
        for line in caption["headline"]:
            self.headline(draw, line, display, (centered_x(line, display), ty), colors)
            ty += leading
        ty += gap
        if not self.zine:
            rule = ((self.width - self.px(72)) // 2, ty + self.px(4), (self.width + self.px(72)) // 2, ty + self.px(12))
            draw.rounded_rectangle(rule, self.px(4), fill=colors["accent"])
        ty += self.px(m["after"])
        for line in note_lines:
            draw.text((centered_x(line, note_font), ty), line, font=note_font, fill=colors["accentText"])
            ty += note_leading

        # The screen below, framed as the landscape window.
        shot = self.shot(caption)
        sy = top + text_height + self.px(m["screen"])
        margin = self.px(m["margin"])
        scale = min((self.height - self.px(m["bottom"]) - sy) / shot.height, (self.width - 2 * margin) / shot.width,
                    self.max_scale)
        shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.LANCZOS)
        x = (self.width - shot.width) // 2
        self.frame(canvas, draw, shot, x, sy, colors, caption.get("appearance") == "dark", self.px(24), self.px(12),
                   self.px(24))
        return canvas


def for_platform(captions: list[dict], canvas: str) -> list[dict]:
    """The captions as the canvas shows them: an entry's object for its platform (windows, android) replaces its fields."""
    platform = PLATFORMS.get(canvas)
    return [{**caption, **caption.get(platform, {})} if platform else caption for caption in captions]


def mix(base: str, over: str, share: float) -> str:
    """`over` at `share` opacity on `base`, both #RRGGBB."""
    a, b = (tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) for color in (base, over))
    return "#" + "".join(f"{round(x + (y - x) * share):02X}" for x, y in zip(a, b))


class Microsoft:
    """The Microsoft Store's images for one language: each Windows capture of `store.microsoft.shots` centered at its own
    size on a 3840 × 2160 backdrop of its appearance, or with `captioned` (a Settings of the microsoft canvas and the
    language's captions, one per shot) laid out as the Mac's, the caption beside the window."""

    def __init__(self, config: companion.Config, shots: list[str], captures: Path, out: Path,
                 captioned: tuple[Settings, list[dict]] | None = None):
        self.shots, self.captures, self.out, self.captioned = shots, captures, out, captioned
        palette = config.get("screenshots.store.palette", {})
        backdrop = config.get("store.microsoft.backdrop", {})
        self.backdrop = {mode: backdrop.get(mode) or palette.get(mode, {}).get("paper") or DEFAULT_PALETTE[mode]["paper"]
                         for mode in DEFAULT_PALETTE}
        self.shadow = config.get("store.microsoft.shadow")
        self.margin = self.shadow.get("offset", 16) if self.shadow else 0

    def render(self, root: Path) -> None:
        """Checks every capture, then replaces the folder's images, printing each one's path; exits on a bad capture."""
        windows = {}
        for shot in self.shots:
            path = self.captures / f"{shot}.png"
            if not path.is_file():
                sys.exit(f"store-screenshots: no {companion.rel(path, root)} (store.microsoft.shots): capture it first")
            with Image.open(path) as image:
                window = image.convert("RGBA")
            if problem := window_problem(window, self.margin, scaled=bool(self.captioned)):
                sys.exit(f"store-screenshots: {companion.rel(path, root)} {problem}")
            windows[shot] = window
        self.out.mkdir(parents=True, exist_ok=True)
        # import.py uploads every PNG there: an earlier run's must not stand in for a shot that's gone.
        for stale in self.out.glob("*.png"):
            stale.unlink()
        digits = len(str(len(self.shots)))  # 01-… past nine, so the names sort in upload order
        for i, shot in enumerate(self.shots, 1):
            path = self.out / f"{i:0{digits}}-{shot}.png"
            image = self.captioned[0].compose(self.captioned[1][i - 1]) if self.captioned else                 self.compose(windows[shot], appearance(shot))
            image.save(path, optimize=True)
            print(f"{companion.rel(path, root)} ({windows[shot].width}×{windows[shot].height} window)")

    def compose(self, window: Image.Image, mode: str) -> Image.Image:
        width, height = MICROSOFT_CANVAS
        canvas = Image.new("RGB", MICROSOFT_CANVAS, self.backdrop[mode])
        if self.shadow:
            # A hard block of the theme's color behind the window, offset right and down; the two centered together.
            offset = self.margin
            x, y = (width - window.width - offset) // 2, (height - window.height - offset) // 2
            color = self.shadow.get(mode) or "#000000"
            ImageDraw.Draw(canvas).rectangle((x + offset, y + offset, x + offset + window.width - 1, y + offset + window.height - 1), fill=color)
            canvas.paste(window, (x, y), window)
            return canvas
        x, y = (width - window.width) // 2, (height - window.height) // 2
        # A soft shadow a little below the window, like Windows' own.
        shadow = Image.new("L", MICROSOFT_CANVAS, 0)
        ImageDraw.Draw(shadow).rectangle((x, y + 16, x + window.width, y + window.height + 16), fill=90 if mode == "dark" else 60)
        shadow = shadow.filter(ImageFilter.GaussianBlur(40))
        canvas.paste(Image.new("RGB", MICROSOFT_CANVAS, "#000000"), (0, 0), shadow)
        canvas.paste(window, (x, y), window)
        return canvas


def appearance(shot: str) -> str:
    match = APPEARANCE.search(shot)
    return match.group(1) if match else "light"


def window_problem(window: Image.Image, margin: int = 0, scaled: bool = False) -> str | None:
    """Why a capture can't be a Microsoft Store image (with `margin` px of hard shadow beside it; `scaled`: the
    captioned layout, which fits it to the canvas), or None."""
    width, height = MICROSOFT_CANVAS
    if not scaled and (window.width + margin > width or window.height + margin > height):
        return (f"is {window.width}×{window.height}, larger than the {width}×{height} canvas (captured at a display scale"
                " over 200%?)")
    # Hidden captures can land before the window draws: a title bar over plain white or black.
    if window.convert("RGB").crop((0, window.height // 4, window.width, window.height)).getcolors(1):
        return "is one flat color below its top quarter: captured before the window drew? Capture it again"
    return None


# Japanese and Chinese have no spaces between words: their lines break between characters, but not before closing
# punctuation or after opening punctuation, nor inside a katakana or Latin word; Korean wraps at spaces like Latin text.
NO_LINE_START = "、。，．！？：；」』）〕】〉》”’ー…・,.!?:;)"
NO_LINE_START_ZH = "的了们"
NO_LINE_END = "「『（〔【〈《“‘("
CLAUSE_END = "、。，！？；"


def breaks_anywhere(language: str) -> bool:
    return language.split("-")[0] in ("ja", "zh")


def wrap(draw, text, font, width, language="en"):
    """Lines of `text` at most `width` wide, balanced so the last isn't a lone word. In Japanese and Chinese, after a
    comma or a full stop, else (Japanese) after the particle that ends a phrase, rather than inside a word, when that
    takes no more lines."""
    lines = balanced(draw, pieces(text, language), font, width)
    if breaks_anywhere(language):
        for units in (clauses(text, language), phrases(text, language)):
            coarser = balanced(draw, units, font, width)
            if len(coarser) <= len(lines) and all(draw.textlength(line, font=font) <= width for line in coarser):
                return coarser
    return lines


def balanced(draw, units, font, width):
    """Greedy wrap of (piece, separator) units, then the narrowest width that takes as many lines."""
    count = len(greedy(draw, units, font, width))
    lo, hi = 0, width
    while hi - lo > 4:
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if len(greedy(draw, units, font, mid)) == count else (mid, hi)
    return greedy(draw, units, font, hi)


def greedy(draw, units, font, width):
    lines, line = [], ""
    for piece, separator in units:
        trial = f"{line}{separator}{piece}" if line else piece
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = piece
    return lines + [line]


def run_kind(char):
    """A katakana word (リマインダー) or a Latin one (CSV) stays on one line."""
    if "゠" <= char <= "ヿ":
        return "katakana"
    return "latin" if char.isascii() and char.isalnum() else None


def pieces(text, language="en"):
    """The units a line may break between, each with the separator that joins it to the one before: words, or in
    Japanese and Chinese characters, joined by a space only where the text has one (CSV 导出)."""
    if not breaks_anywhere(language):
        return [(word, " ") for word in text.split()]
    no_start = NO_LINE_START + (NO_LINE_START_ZH if language.startswith("zh") else "")
    out, separator = [], ""
    for char in text:
        if char.isspace():
            separator = " "
            continue
        kind, last = run_kind(char), out[-1][0][-1] if out else ""
        if out and (char in no_start or last in NO_LINE_END or (not separator and kind and run_kind(last) == kind)):
            out[-1] = (out[-1][0] + separator + char, out[-1][1])
        else:
            out.append((char, separator))
        separator = ""
    return out


def phrases(text, language="ja"):
    """Japanese in phrases: a line breaks only after hiragana (a particle or an ending) before the next word."""
    out = []
    for piece, separator in pieces(text, language):
        last = out[-1][0][-1] if out else ""
        if out and not separator and (not (is_hiragana(last) or last in CLAUSE_END) or is_hiragana(piece[0])):
            out[-1] = (out[-1][0] + piece, out[-1][1])
        else:
            out.append((piece, separator))
    return out


def is_hiragana(char):
    return "ぁ" <= char <= "ゟ"


def clauses(text, language="ja"):
    """Japanese and Chinese in phrases that end at a comma or a full stop."""
    out, closed = [], True
    for piece, separator in pieces(text, language):
        if closed:
            out.append((piece, separator))
        else:
            out[-1] = (out[-1][0] + separator + piece, out[-1][1])
        closed = any(char in CLAUSE_END for char in piece)
    return out


def rounded(image, radius):
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius, fill=255)
    return mask


def play_problem(image):
    """What Google Play would refuse about an image's size, or None."""
    short, long = sorted(image.size)
    if not PLAY_SIDE[0] <= short and long <= PLAY_SIDE[1]:
        return f"{image.width}×{image.height}: Google Play takes each side between {PLAY_SIDE[0]} and {PLAY_SIDE[1]} px"
    if long > short * PLAY_RATIO:
        return f"{image.width}×{image.height}: Google Play takes no side more than {PLAY_RATIO}× the other"
    return None


def capture(config: companion.Config, language: str, captions: list[dict]) -> Path:
    """The Mac windows `captions` show, captured in `language` (screenshots.sh) into build/store-captures/apple."""
    folder = config.root / "build" / "store-captures" / "apple" / language
    shots = ",".join(dict.fromkeys(caption["shot"] for caption in captions))
    command = companion.shell_command(TOOLS / "screenshots.sh")
    if command is None:
        sys.exit("store-screenshots: capturing needs bash (Git for Windows); pass a captures folder")
    if subprocess.run([*command, "--only", shots, "--front", str(folder), language]).returncode:
        sys.exit(f"store-screenshots: capturing the {language} windows failed")
    return folder


def windows_capture(config: companion.Config, language: str, shots: list[str]) -> Path:
    """The Windows windows `shots` show, captured in `language` into build/store-captures/microsoft."""
    folder = config.root / "build" / "store-captures" / "microsoft" / language
    folder.mkdir(parents=True, exist_ok=True)
    # The capture only adds shots: an earlier run's must not stand in for one that didn't render.
    for stale in folder.glob("*.png"):
        stale.unlink()
    if subprocess.run(windows_capture_command(config, folder, language, shots)).returncode:
        sys.exit(f"store-screenshots: capturing the {language} windows failed")
    return folder


def windows_capture_command(config: companion.Config, folder: Path, language: str, shots: list[str],
                            windows: bool = os.name == "nt") -> list[str]:
    """screenshots.ps1 (the app's own <windows.dir>/scripts/screenshots.ps1, else Laching's) for `shots`, shown in front
    as in use: run here on Windows, in the VM through vm.sh elsewhere."""
    args = ["-Only", ",".join(dict.fromkeys(APPEARANCE.sub("", shot) for shot in shots)), "-Front", "-Language", language]
    if not windows:
        return [str(TOOLS / "windows" / "vm.sh"), "screenshots", str(folder), "--", *args]
    own = config.path("windows.dir")
    script = own / "scripts" / "screenshots.ps1" if own else None
    script = script if script and script.is_file() else TOOLS / "windows" / "screenshots.ps1"
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Out", str(folder), *args]


def microsoft(parser: argparse.ArgumentParser, args: argparse.Namespace, config: companion.Config) -> int:
    shots = config.get("store.microsoft.shots")
    if not shots:
        parser.error("--canvas microsoft needs store.microsoft.shots in companion.json: the Windows captures in upload order")
    languages = config.languages if "all" in (args.lang or []) else list(dict.fromkeys(args.lang or [config.source_language]))
    if missing := [lang for lang in languages if lang not in config.languages]:
        parser.error(f"{', '.join(missing)} isn't in languages.all ({', '.join(config.languages)})")
    if args.captures and len(languages) > 1:
        parser.error("a captures folder is one language's: name one --lang")
    out = args.out or config.require_path("store.dir") / config.get("store.microsoft.screenshots", "microsoft/screenshots")
    captions = captioned_shots(config, languages, shots) if config.get("store.microsoft.captioned") else {}
    for language in languages:
        if args.captures:
            captures = args.captures
        elif language != config.source_language:
            captures = windows_capture(config, language, shots)
        else:
            captures = config.require_path("windows.screenshots")
        layout = (Settings(config, "microsoft", captures, out / language, language), captions[language]) if captions else None
        Microsoft(config, shots, captures, out / language, layout).render(config.root)
    return 0


def captioned_shots(config: companion.Config, languages: list[str], shots: list[str]) -> dict[str, list[dict]]:
    """Each language's captions as Windows shows them (an entry's `windows` object over its fields), one per shot of
    store.microsoft.shots and in its order; exits before anything is captured when they don't match."""
    captions = json.loads(config.require_path("screenshots.store.captions").read_text(encoding="utf-8"))
    if missing := [lang for lang in languages if lang not in captions]:
        sys.exit(f"store-screenshots: no captions for {', '.join(missing)} in store.captions (store.microsoft.captioned)")
    out = {}
    for language in languages:
        entries = for_platform(captions[language], "microsoft")
        named = [f"{entry['shot']}-{entry.get('appearance', 'light')}" for entry in entries]
        if named != shots:
            sys.exit(f"store-screenshots: the {language} captions show {', '.join(named)}, store.microsoft.shots"
                     f" {', '.join(shots)}: with store.microsoft.captioned they go one to one (a `windows` object"
                     " changes an entry's shot)")
        out[language] = entries
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="?", type=Path, help="one language's captures (default: see above)")
    parser.add_argument("--canvas", choices=CANVASES, default="mac")
    parser.add_argument("--lang", action="append",
                        help="a language of the captions (microsoft: of the app), or all (repeatable; default: the source)")
    parser.add_argument("--out", type=Path, help="instead of the canvas's folder (see above): the images go to DIR/<lang>")
    args = parser.parse_args(argv)
    config = companion.load()
    if args.canvas == "microsoft":
        return microsoft(parser, args, config)
    captions = json.loads(config.require_path("screenshots.store.captions").read_text(encoding="utf-8"))
    languages = list(captions) if "all" in (args.lang or []) else list(dict.fromkeys(args.lang or [config.source_language]))
    if missing := [lang for lang in languages if lang not in captions]:
        parser.error(f"no captions for {', '.join(missing)} in store.captions (it has {', '.join(captions)})")
    if args.captures and len(languages) > 1:
        parser.error("a captures folder is one language's: name one --lang")
    out = args.out or config.require_path("screenshots.store.out") / ("" if args.canvas == "mac" else args.canvas)
    for language in languages:
        if args.captures:
            captures = args.captures
        elif args.canvas == "mac" and language != config.source_language:
            captures = capture(config, language, captions[language])
        else:
            captures = config.require_path("screenshots.out")
        Settings(config, args.canvas, captures, out / language, language).render(captions[language], config.root)
    return 0


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
