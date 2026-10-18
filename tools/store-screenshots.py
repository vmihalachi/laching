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
productivity claims.

Canvases: `mac` is landscape, the window on the right and the text beside it, drawn for 2880 × 1800 and scaled to
`store.size`; `android-phone` (1080 × 1920, 9:16) stacks the caption above the screen, as the iPhone images do, every
shot of a language reserving its tallest caption's height so the screens line up; `android-tablet` (2560 × 1600, a
10-inch tablet) is the landscape layout. `store.canvases` overrides an Android canvas's size; Google Play takes each
side between 320 and 3840 px and no side more than twice the other, which every Android image is checked against.

`microsoft` needs no captions or fonts (Partner Center shows each caption under its image): each Windows capture
named in `store.microsoft.shots` (file names without .png, upload order, one per line of ms.captions) is centered at
its own size, never scaled so its text stays sharp, on a 3840 × 2160 backdrop with a soft shadow. The backdrop is
`store.microsoft.backdrop` (light/dark → color, by the capture's -light or -dark), else the paper of `store.palette`.
The images go to <store.dir>/<store.microsoft.screenshots, microsoft/screenshots by default>/<language> (--out DIR:
DIR/<language>), named <n>-<shot>.png, where import.py takes them; the folder's old PNGs go first. --lang all is every
language of the app. The captures: the folder given, else windows.screenshots for the source language and, for any
other, the windows the shots need captured in that language first (`screenshots.ps1 -Only … -Front -Language` into
build/store-captures/microsoft/<language>; from a Mac through `windows/vm.sh screenshots`). A capture larger than the
canvas, or one flat color below its top quarter (a window captured before it drew), stops the run before anything is
written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
# Captures are at 2x; enlarging past this gets soft.
MAX_SCALE = 1.8


class Settings:
    """One canvas's composition for one language: its size, colors and fonts from companion.json, and where the
    captures are read from and the images written to."""

    def __init__(self, config: companion.Config, canvas: str, captures: Path, out: Path):
        store = config.require("screenshots.store")
        self.canvas, self.captures, self.out = canvas, captures, out
        if canvas == "mac":
            self.width, self.height = (int(v) for v in store.get("size", [2880, 1800]))
        else:
            self.width, self.height = (int(v) for v in store.get("canvases", {}).get(canvas, ANDROID_CANVASES[canvas]))
        self.portrait = canvas == "android-phone"
        # The layouts' numbers are for a 2880-wide (landscape) or 1080-wide (portrait) canvas.
        self.unit = self.width / (1080 if self.portrait else 2880)
        self.palette = {mode: {**DEFAULT_PALETTE[mode], **store.get("palette", {}).get(mode, {})} for mode in DEFAULT_PALETTE}
        self.headline_font = config.require_path("screenshots.store.font")
        self.note_font = config.path("screenshots.store.noteFont") or self.headline_font

    def render(self, captions: list[dict], root: Path) -> None:
        """Writes the language's images, printing each one's path; exits when Google Play would refuse one."""
        self.out.mkdir(parents=True, exist_ok=True)
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

    def compose(self, caption):
        colors = self.palette[caption.get("appearance", "light")]
        canvas = Image.new("RGB", (self.width, self.height), colors["paper"])
        draw = ImageDraw.Draw(canvas)

        # The window on the right, with rounded corners and a soft shadow like a macOS window.
        shot = Image.open(self.captures / f"{caption['shot']}-{caption.get('appearance', 'light')}.png").convert("RGB")
        scale = min(self.px(1440) / shot.height, self.px(1360) / shot.width, MAX_SCALE)
        shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.LANCZOS)
        x, y = self.width - shot.width - self.px(200), (self.height - shot.height) // 2
        radius = self.px(36)
        shadow = Image.new("L", (self.width, self.height), 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (x, y + self.px(24), x + shot.width, y + shot.height + self.px(24)), radius,
            fill=90 if caption.get("appearance") == "dark" else 60,
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(self.px(40)))
        canvas.paste(Image.new("RGB", (self.width, self.height), "#000000"), (0, 0), shadow)
        canvas.paste(shot, (x, y), rounded(shot, radius))
        draw.rounded_rectangle((x, y, x + shot.width - 1, y + shot.height - 1), radius, outline=colors["ink"] + "33", width=2)

        # Headline and note in the left column, centered on the window.
        left, column = self.px(200), x - self.px(200) - self.px(140)
        size = self.px(168)
        while True:
            display = ImageFont.truetype(str(self.headline_font), size)
            if max(draw.textlength(line, font=display) for line in caption["headline"]) <= column:
                break
            size -= 4
        note_font = ImageFont.truetype(str(self.note_font), self.px(76))
        leading, note_leading = round(size * 1.12), self.px(100)
        note_lines = wrap(draw, caption["note"], note_font, column)
        gap = round(size * 0.2)  # clears the descenders of the last headline line
        block = len(caption["headline"]) * leading + gap + self.px(56) + len(note_lines) * note_leading
        ty = (self.height - block) // 2
        for line in caption["headline"]:
            draw.text((left - display.getbbox(line)[0], ty), line, font=display, fill=colors["ink"])
            ty += leading
        # A short accent rule between headline and note.
        ty += gap
        draw.rounded_rectangle((left, ty + self.px(8), left + self.px(160), ty + self.px(26)), self.px(9), fill=colors["accent"])
        ty += self.px(56)
        for line in note_lines:
            draw.text((left - note_font.getbbox(line)[0], ty), line, font=note_font, fill=colors["accentText"])
            ty += note_leading
        return canvas

    def portrait_text(self, draw, caption):
        """The headline's font (shrunk until its widest line fits the column), the wrapped note and the block's height."""
        column = self.width - 2 * self.px(72)
        size = self.px(72)
        while True:
            display = ImageFont.truetype(str(self.headline_font), size)
            if max(draw.textlength(line, font=display) for line in caption["headline"]) <= column:
                break
            size -= 2
        note_font = ImageFont.truetype(str(self.note_font), self.px(40))
        note_lines = wrap(draw, caption["note"], note_font, column)
        leading, note_leading = round(size * 1.12), self.px(52)
        gap = round(size * 0.2)
        height = len(caption["headline"]) * leading + gap + self.px(32) + len(note_lines) * note_leading
        return display, leading, note_font, note_lines, note_leading, gap, height

    def compose_portrait(self, caption, text_height):
        """A phone canvas: the headline and note centered at the top, the screen below them, scaled to what is left."""
        colors = self.palette[caption.get("appearance", "light")]
        canvas = Image.new("RGB", (self.width, self.height), colors["paper"])
        draw = ImageDraw.Draw(canvas)
        display, leading, note_font, note_lines, note_leading, gap, height = self.portrait_text(draw, caption)

        def centered_x(text, font):
            left, _, right, _ = font.getbbox(text)
            return (self.width - (right - left)) // 2 - left

        top = self.px(96)
        ty = top + (text_height - height) // 2
        for line in caption["headline"]:
            draw.text((centered_x(line, display), ty), line, font=display, fill=colors["ink"])
            ty += leading
        ty += gap
        rule = ((self.width - self.px(72)) // 2, ty + self.px(4), (self.width + self.px(72)) // 2, ty + self.px(12))
        draw.rounded_rectangle(rule, self.px(4), fill=colors["accent"])
        ty += self.px(32)
        for line in note_lines:
            draw.text((centered_x(line, note_font), ty), line, font=note_font, fill=colors["accentText"])
            ty += note_leading

        # The screen below, with rounded corners and a soft shadow, as the Mac window.
        shot = Image.open(self.captures / f"{caption['shot']}-{caption.get('appearance', 'light')}.png").convert("RGB")
        sy = top + text_height + self.px(48)
        scale = min((self.height - self.px(72) - sy) / shot.height, (self.width - 2 * self.px(72)) / shot.width, MAX_SCALE)
        shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.LANCZOS)
        x = (self.width - shot.width) // 2
        radius = self.px(24)
        shadow = Image.new("L", (self.width, self.height), 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (x, sy + self.px(12), x + shot.width, sy + shot.height + self.px(12)), radius,
            fill=90 if caption.get("appearance") == "dark" else 60,
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(self.px(24)))
        canvas.paste(Image.new("RGB", (self.width, self.height), "#000000"), (0, 0), shadow)
        canvas.paste(shot, (x, sy), rounded(shot, radius))
        draw.rounded_rectangle((x, sy, x + shot.width - 1, sy + shot.height - 1), radius, outline=colors["ink"] + "33", width=2)
        return canvas


class Microsoft:
    """The Microsoft Store's images for one language: each Windows capture of `store.microsoft.shots` centered at its own
    size on a 3840 × 2160 backdrop of its appearance."""

    def __init__(self, config: companion.Config, shots: list[str], captures: Path, out: Path):
        self.shots, self.captures, self.out = shots, captures, out
        palette = config.get("screenshots.store.palette", {})
        backdrop = config.get("store.microsoft.backdrop", {})
        self.backdrop = {mode: backdrop.get(mode) or palette.get(mode, {}).get("paper") or DEFAULT_PALETTE[mode]["paper"]
                         for mode in DEFAULT_PALETTE}

    def render(self, root: Path) -> None:
        """Checks every capture, then replaces the folder's images, printing each one's path; exits on a bad capture."""
        windows = {}
        for shot in self.shots:
            path = self.captures / f"{shot}.png"
            if not path.is_file():
                sys.exit(f"store-screenshots: no {companion.rel(path, root)} (store.microsoft.shots): capture it first")
            with Image.open(path) as image:
                window = image.convert("RGBA")
            if problem := window_problem(window):
                sys.exit(f"store-screenshots: {companion.rel(path, root)} {problem}")
            windows[shot] = window
        self.out.mkdir(parents=True, exist_ok=True)
        # import.py uploads every PNG there: an earlier run's must not stand in for a shot that's gone.
        for stale in self.out.glob("*.png"):
            stale.unlink()
        digits = len(str(len(self.shots)))  # 01-… past nine, so the names sort in upload order
        for i, shot in enumerate(self.shots, 1):
            path = self.out / f"{i:0{digits}}-{shot}.png"
            self.compose(windows[shot], appearance(shot)).save(path, optimize=True)
            print(f"{companion.rel(path, root)} ({windows[shot].width}×{windows[shot].height} window)")

    def compose(self, window: Image.Image, mode: str) -> Image.Image:
        width, height = MICROSOFT_CANVAS
        canvas = Image.new("RGB", MICROSOFT_CANVAS, self.backdrop[mode])
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


def window_problem(window: Image.Image) -> str | None:
    """Why a capture can't be a Microsoft Store image, or None."""
    width, height = MICROSOFT_CANVAS
    if window.width > width or window.height > height:
        return (f"is {window.width}×{window.height}, larger than the {width}×{height} canvas (captured at a display scale"
                " over 200%?)")
    # Hidden captures can land before the window draws: a title bar over plain white or black.
    if window.convert("RGB").crop((0, window.height // 4, window.width, window.height)).getcolors(1):
        return "is one flat color below its top quarter: captured before the window drew? Capture it again"
    return None


def wrap(draw, text, font, width):
    """Greedy wrap, then balanced so the last line isn't a lone word."""

    def greedy(limit):
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= limit or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        return lines + [line]

    count = len(greedy(width))
    lo, hi = 0, width
    while hi - lo > 4:
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if len(greedy(mid)) == count else (mid, hi)
    return greedy(hi)


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
    for language in languages:
        if args.captures:
            captures = args.captures
        elif language != config.source_language:
            captures = windows_capture(config, language, shots)
        else:
            captures = config.require_path("windows.screenshots")
        Microsoft(config, shots, captures, out / language).render(config.root)
    return 0


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
        Settings(config, args.canvas, captures, out / language).render(captions[language], config.root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
