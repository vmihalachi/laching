#!/usr/bin/env python3
"""Builds a companion's display font from a pinned upstream variable font (`fonts` in companion.json).

Downloads the font and its license at their pinned URLs, checks their sha256, instances each weight the app uses
(`fonts.weights`, e.g. {"Medium": 500, "SemiBold": 600}) at the font's default for every other axis, composes letters the face lacks from its own base letters
and marks (`fonts.compose`), optionally drops the kerning pairs that start with the space (some faces pull a following
V, W or Y into the space, `fonts.dropSpaceKerning`), and checks every character in `fonts.required` is there. The
modification is noted in each font's description. Outputs, in `fonts.out`: `<family>-<weight name>.ttf` and the license.

`fonts.faces` adds faces from more variable fonts under the same license (an upright beside an italic), each
`{"family", "font": [url, sha256], "weights"}`; `required`, `compose` and `dropSpaceKerning` apply to every face.

    python3 -m venv /tmp/fontenv && /tmp/fontenv/bin/pip install fonttools
    /tmp/fontenv/bin/python laching/tools/fonts.py            # write the files
    /tmp/fontenv/bin/python laching/tools/fonts.py --check    # exit 1 when a committed file lacks a glyph

`fonts.compose` maps a character to `[base glyph, mark glyph, "above" | "below", alias characters…]`: the mark goes
over the base as far above it as it sits over a lowercase "a" ("above"), or 80 % size under its foot ("below", like the
comma under Ș); aliases map more code points to the same glyph (Ş for Ș). Needs fontTools (`pip install fonttools`);
everything else is the standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion  # noqa: E402

BELOW_SCALE, BELOW_GAP = 0.8, 70


class Face:
    """One variable font and the static weights instanced from it."""

    def __init__(self, spec: dict, out: Path):
        self.family: str = spec["family"]
        self.font = tuple(spec["font"][:2])
        self.weights: dict[str, float] = spec["weights"]
        self.out = out

    def path(self, label: str) -> Path:
        return self.out / f"{self.family}-{label}.ttf"


class Plan:
    """What `fonts` in companion.json asks for."""

    def __init__(self, config: companion.Config):
        fonts = config.get("fonts")
        if not fonts:
            sys.exit("fonts: companion.json has no fonts section (null means the app ships none)")
        self.out = config.require_path("fonts.out")
        self.faces = [Face(spec, self.out) for spec in [fonts, *fonts.get("faces", [])]]
        self.family = self.faces[0].family
        self.font = self.faces[0].font
        self.license = tuple(fonts["license"][:2])
        self.license_file = fonts.get("licenseFile") or f"OFL-{self.family}.txt"
        self.weights = self.faces[0].weights
        self.required: str = fonts.get("required", "")
        self.compose: dict[str, list[str]] = fonts.get("compose", {})
        self.drop_space_kerning = bool(fonts.get("dropSpaceKerning", False))
        self.app = config.require("displayName")

    def path(self, label: str) -> Path:
        return self.faces[0].path(label)

    def description(self, family: str | None = None) -> str:
        changes = ["one static weight"]
        if self.compose:
            changes.append(f"plus {' '.join(self.compose)} composed from the font's own letters and marks")
        if self.drop_space_kerning:
            changes.append("no kerning after the space")
        return f"Modified version of {family or self.family} (variable): {', '.join(changes)}, for {self.app}."


def download(url: str, sha256: str) -> bytes:
    data = urllib.request.urlopen(url).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"{url}: expected sha256 {sha256}, got {digest}")
    return data


def glyph_name(char: str) -> str:
    return f"uni{ord(char):04X}"


def map_codepoint(font, codepoint: int, glyph: str):
    for table in font["cmap"].tables:
        if table.isUnicode():
            table.cmap[codepoint] = glyph


def add_composite(font, name: str, codepoints: list[int], base: str, mark: str, dx: int, dy: int, scale: float = 1):
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    glyf = font["glyf"]
    pen = TTGlyphPen(glyf)
    pen.addComponent(base, (1, 0, 0, 1, 0, 0))
    pen.addComponent(mark, (scale, 0, 0, scale, dx, dy))
    if name not in glyf.keys():
        font.setGlyphOrder(font.getGlyphOrder() + [name])
    glyf[name] = pen.glyph()
    glyf[name].recalcBounds(glyf)
    advance, _ = font["hmtx"][base]
    font["hmtx"][name] = (advance, glyf[name].xMin)
    for codepoint in codepoints:
        map_codepoint(font, codepoint, name)
    if "GDEF" in font and font["GDEF"].table.GlyphClassDef:
        font["GDEF"].table.GlyphClassDef.classDefs[name] = 1


def bounds(font, glyph: str) -> tuple[int, int, int, int]:
    g = font["glyf"][glyph]
    g.recalcBounds(font["glyf"])
    return g.xMin, g.yMin, g.xMax, g.yMax


def foot_centre(font, base: str) -> float:
    """Horizontal centre of the part of `base` that sits on the baseline (the stem of t, the bowl of s)."""
    coords, _, _ = font["glyf"][base].getCoordinates(font["glyf"])
    xs = [x for x, y in coords if y < 250]
    return (min(xs) + max(xs)) / 2


def compose(font, plan: Plan) -> None:
    """Adds every `fonts.compose` character the font lacks."""
    cmap = font.getBestCmap()
    for char, spec in plan.compose.items():
        if ord(char) in cmap:
            continue
        base, mark, position, *aliases = spec
        codepoints = [ord(char)] + [ord(alias) for alias in aliases if ord(alias) not in cmap]
        mark_x0, mark_y0, mark_x1, mark_y1 = bounds(font, mark)
        if position == "above":
            gap = mark_y0 - bounds(font, "a")[3]  # the mark sits this far over a lowercase letter
            x0, _, x1, top = bounds(font, base)
            dx = round((x0 + x1) / 2 - (mark_x0 + mark_x1) / 2)
            dy = round(top + gap - mark_y0)
            add_composite(font, glyph_name(char), codepoints, base, mark, dx, dy)
        elif position == "below":
            _, base_bottom, _, _ = bounds(font, base)
            dx = foot_centre(font, base) - (mark_x0 + mark_x1) / 2 * BELOW_SCALE
            dy = -BELOW_GAP + min(0, base_bottom) - mark_y1 * BELOW_SCALE
            add_composite(font, glyph_name(char), codepoints, base, mark, round(dx), round(dy), BELOW_SCALE)
        else:
            raise SystemExit(f"fonts.compose.{char}: position must be above or below, not {position}")


def space_pair_subtables(font):
    """The pair-kerning subtables whose first glyph can be the space, with the space's glyph name."""
    space = font.getBestCmap()[0x20]
    if "GPOS" not in font or font["GPOS"].table.LookupList is None:
        return space, []
    found = []
    for lookup in font["GPOS"].table.LookupList.Lookup:
        for sub in lookup.SubTable:
            if lookup.LookupType == 9:  # an extension wraps the real subtable
                if sub.ExtensionLookupType != 2:
                    continue
                sub = sub.ExtSubTable
            elif lookup.LookupType != 2:
                continue
            if space in sub.Coverage.glyphs:
                found.append(sub)
    return space, found


def unkern_space(font) -> None:
    """Drops every kerning pair that starts with the space."""
    space, subtables = space_pair_subtables(font)
    for sub in subtables:
        index = sub.Coverage.glyphs.index(space)
        del sub.Coverage.glyphs[index]
        if sub.Format == 1:  # glyph pairs: one pair set per covered glyph
            del sub.PairSet[index]
            sub.PairSetCount = len(sub.PairSet)
        # Format 2 (class pairs) kerns only covered glyphs, so leaving the coverage is enough.


def build(data: bytes, face: Face, label: str, weight: float, plan: Plan):
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(io.BytesIO(data))
    axes = {axis.axisTag: axis.defaultValue for axis in font["fvar"].axes} if "fvar" in font else {}
    axes["wght"] = weight
    static = instancer.instantiateVariableFont(font, axes, updateFontNames=True)
    compose(static, plan)
    if plan.drop_space_kerning:
        unkern_space(static)
    static["name"].setName(plan.description(face.family), 10, 3, 1, 0x409)
    postscript = static["name"].getDebugName(6)
    if postscript != f"{face.family}-{label}":
        raise SystemExit(f"unexpected PostScript name {postscript}; the app registers {face.family}-{label}")
    return static


def missing(path: Path, required: str) -> list[str]:
    from fontTools.ttLib import TTFont

    cmap = TTFont(path).getBestCmap()
    return [c for c in required if ord(c) not in cmap]


def check(plan: Plan) -> int:
    from fontTools.ttLib import TTFont

    status = 0
    for face, label in [(face, label) for face in plan.faces for label in face.weights]:
        path = face.path(label)
        if not path.exists():
            print(f"missing {path}")
            status = 1
            continue
        gaps = missing(path, plan.required)
        if gaps:
            print(f"{path.name} lacks {' '.join(gaps)}")
            status = 1
        if plan.drop_space_kerning and space_pair_subtables(TTFont(path))[1]:
            print(f"{path.name} still kerns after the space")
            status = 1
    if not (plan.out / plan.license_file).exists():
        print(f"missing {plan.license_file}")
        status = 1
    print("ok" if status == 0 else "fonts need regenerating: python3 laching/tools/fonts.py")
    return status


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when a committed file lacks a glyph")
    args = parser.parse_args(argv)
    plan = Plan(companion.load())
    if args.check:
        return check(plan)
    plan.out.mkdir(parents=True, exist_ok=True)
    for face in plan.faces:
        data = download(*face.font)
        for label, weight in face.weights.items():
            path = face.path(label)
            build(data, face, label, weight, plan).save(path)
            gaps = missing(path, plan.required)
            if gaps:
                raise SystemExit(f"{path.name} still lacks {' '.join(gaps)}")
            print(path)
    (plan.out / plan.license_file).write_bytes(download(*plan.license))
    print(plan.out / plan.license_file)
    return 0


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
