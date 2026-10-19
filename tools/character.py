#!/usr/bin/env python3
"""Compiles a companion's character art and rig into the data both cores draw from.

    python3 laching/tools/character.py [--skin NAME …] [--check]   # an app's skins, from companion.json
    python3 tools/character.py --design DIR --skin NAME --out JSON [--swift-out FILE --module NAME] [--mode vector|raster]
                               [--assets DIR --windows-assets DIR] [--palette FILE] [--moods a,b,…] [--check]
    python3 tools/character.py preview --design DIR --skin NAME OUT.svg [--mode raster] [--png]   # the review sheet

Without `--design` it reads the app's `companion.json`: every skin folder under `characters.design` (or the `--skin`s
named) goes to `<conformance.dir>/characters/<skin>.json` and, with `characters.swiftOut`, to
`<swiftOut>/<Skin>Art.swift`, in `characters.mode` with `characters.moods`, `characters.paletteFile` and, for raster
skins, `characters.assets` and `characters.windowsAssets`.

A skin is a folder `<design>/<skin>/` with `glyph.svg` (the menu bar frames, a black vector line drawing for every
skin) and `<skin>.rig.json`. `--out` gets the art as JSON (the format `CharacterArtJSON` reads), `--swift-out` the same
art as `extension CharacterArt { public static let <skinInCamelCase> }`, importing `--module`.

**Vector** skins (the default) draw their parts from SVGs: in each, the first `<g id="…">` without a named ancestor
is a part (or, in `glyph.svg`, a frame), and every drawable element inside it is one shape; `<circle id="pivot-<name>">`
marks a pivot and isn't drawn. Colors must be listed in the palette (`<design>/palette.json` unless `--palette`), which
maps each hex value to a paint role. Every transform is flattened and every curve becomes a cubic Bézier, so the output
paths use only M, L, C and Z.

**Raster** skins (`--mode raster`) draw each part from `layers/<part>@2x.png` (and optionally `@3x`). The rig adds
`viewBox: [w, h]`, `pivots: {name: [x, y]}` and `layers: {<part>: {frame: [x, y, w, h]}}` in view-box units; each PNG
must measure frame × scale pixels. A part is written as `{"image": "<skin>.<part>", "frame": […]}`; `--assets` gets an
imageset per layer (an Xcode asset catalog folder) and `--windows-assets` the `scale-200`/`scale-300` PNGs.

The rig accepts `cheeks: true` / `zz: true` in a mood as sugar for `headExtras: ["cheeks"]` / `overlays: ["zz"]`, and
requires `bubble: {pivot, tuck}`: where a speech bubble aims and how far it tucks in (a fraction of the width).
`--check` writes nothing and exits 1 naming any stale output (the PNG copies compared by hash). Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion  # noqa: E402

MOODS = ["awake", "talking", "shushed", "hushedForCall", "hushedFullScreen", "asleep", "offHours"]
SCALES = (2, 3)
KAPPA = 0.5522847498
SVG_NS = "http://www.w3.org/2000/svg"
DRAWABLE = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
IGNORED = {"metadata", "title", "desc", "defs", "linearGradient", "radialGradient", "stop"}
UNSUPPORTED = {"text", "tspan", "filter", "mask", "image", "use", "pattern", "clipPath", "symbol", "marker", "foreignObject", "switch"}
INHERITED = ("fill", "stroke", "stroke-width", "fill-rule")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CompileError(Exception):
    pass


# MARK: Affine transforms, (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(m, n):
    """m·n: n is applied first, then m (a parent's transform times its child's, as SVG nests them)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(text: str, where: str):
    matrix = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        values = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(values) == 6:
            step = tuple(values)
        elif name == "translate":
            step = (1, 0, 0, 1, values[0], values[1] if len(values) > 1 else 0)
        elif name == "scale":
            sx = values[0]
            sy = values[1] if len(values) > 1 else sx
            step = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            r = math.radians(values[0])
            step = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)
            if len(values) == 3:
                cx, cy = values[1], values[2]
                step = multiply(multiply((1, 0, 0, 1, cx, cy), step), (1, 0, 0, 1, -cx, -cy))
        elif name == "skewX":
            step = (1, 0, math.tan(math.radians(values[0])), 1, 0, 0)
        elif name == "skewY":
            step = (1, math.tan(math.radians(values[0])), 0, 1, 0, 0)
        else:
            raise CompileError(f"{where}: unsupported transform '{name}'")
        matrix = multiply(matrix, step)
    return matrix


# MARK: Paths (absolute M/L/C/Z in local coordinates)


class _Scanner:
    NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")

    def __init__(self, text: str, where: str):
        self.text = text
        self.i = 0
        self.where = where

    def skip(self):
        while self.i < len(self.text) and self.text[self.i] in " ,\t\r\n":
            self.i += 1

    def done(self) -> bool:
        self.skip()
        return self.i >= len(self.text)

    def at_number(self) -> bool:
        self.skip()
        return self.i < len(self.text) and (self.text[self.i].isdigit() or self.text[self.i] in "+-.")

    def command(self) -> str:
        self.skip()
        c = self.text[self.i]
        if not c.isalpha():
            raise CompileError(f"{self.where}: expected a path command at '{self.text[self.i:self.i + 12]}'")
        self.i += 1
        return c

    def number(self) -> float:
        self.skip()
        m = self.NUMBER.match(self.text, self.i)
        if not m:
            raise CompileError(f"{self.where}: expected a number at '{self.text[self.i:self.i + 12]}'")
        self.i = m.end()
        return float(m.group())

    def flag(self) -> bool:
        self.skip()
        c = self.text[self.i : self.i + 1]
        if c not in ("0", "1"):
            raise CompileError(f"{self.where}: expected an arc flag at '{self.text[self.i:self.i + 12]}'")
        self.i += 1
        return c == "1"


def arc_to_cubics(x1, y1, rx, ry, phi_degrees, large, sweep, x2, y2):
    """SVG elliptical arc (endpoint form) as cubic segments of at most 90°, each (c1, c2, end)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [((x1, y1), (x2, y2), (x2, y2))]
    phi = math.radians(phi_degrees)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    count = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / count
    t = 4 / 3 * math.tan(step / 4)

    def point(theta):
        x, y = rx * math.cos(theta), ry * math.sin(theta)
        return (cos_p * x - sin_p * y + cx, sin_p * x + cos_p * y + cy)

    def derivative(theta):
        x, y = -rx * math.sin(theta), ry * math.cos(theta)
        return (cos_p * x - sin_p * y, sin_p * x + cos_p * y)

    segments = []
    for k in range(count):
        a0, a1 = theta1 + k * step, theta1 + (k + 1) * step
        p0, p1 = point(a0), point(a1)
        d0, d1 = derivative(a0), derivative(a1)
        c1 = (p0[0] + t * d0[0], p0[1] + t * d0[1])
        c2 = (p1[0] - t * d1[0], p1[1] - t * d1[1])
        segments.append((c1, c2, p1))
    c1, c2, _ = segments[-1]
    segments[-1] = (c1, c2, (x2, y2))
    return segments


def parse_path(d: str, where: str) -> list:
    s = _Scanner(d, where)
    out = []
    cx = cy = sx = sy = 0.0
    last_cubic = last_quad = None
    command = None
    while not s.done():
        if not s.at_number():
            command = s.command()
        elif command is None:
            raise CompileError(f"{where}: path data must start with a command")
        rel = command.islower()
        op = command.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if op == "Z":
            out.append(["Z"])
            cx, cy = sx, sy
            last_cubic = last_quad = None
            command = None
            continue
        if op == "M":
            cx, cy = ox + s.number(), oy + s.number()
            sx, sy = cx, cy
            out.append(["M", cx, cy])
            command = "l" if rel else "L"
            last_cubic = last_quad = None
            continue
        if op in ("L", "H", "V"):
            if op == "L":
                cx, cy = ox + s.number(), oy + s.number()
            elif op == "H":
                cx = (cx if rel else 0.0) + s.number()
            else:
                cy = (cy if rel else 0.0) + s.number()
            out.append(["L", cx, cy])
            last_cubic = last_quad = None
        elif op in ("C", "S"):
            if op == "C":
                c1 = (ox + s.number(), oy + s.number())
            else:
                c1 = (2 * cx - last_cubic[0], 2 * cy - last_cubic[1]) if last_cubic else (cx, cy)
            c2 = (ox + s.number(), oy + s.number())
            end = (ox + s.number(), oy + s.number())
            out.append(["C", *c1, *c2, *end])
            last_cubic, last_quad = c2, None
            cx, cy = end
        elif op in ("Q", "T"):
            if op == "Q":
                q = (ox + s.number(), oy + s.number())
            else:
                q = (2 * cx - last_quad[0], 2 * cy - last_quad[1]) if last_quad else (cx, cy)
            end = (ox + s.number(), oy + s.number())
            c1 = (cx + 2 / 3 * (q[0] - cx), cy + 2 / 3 * (q[1] - cy))
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
            out.append(["C", *c1, *c2, *end])
            last_quad, last_cubic = q, None
            cx, cy = end
        elif op == "A":
            rx, ry, phi = s.number(), s.number(), s.number()
            large, sweep = s.flag(), s.flag()
            end = (ox + s.number(), oy + s.number())
            for c1, c2, p in arc_to_cubics(cx, cy, rx, ry, phi, large, sweep, *end):
                out.append(["C", *c1, *c2, *p])
            cx, cy = end
            last_cubic = last_quad = None
        else:
            raise CompileError(f"{where}: unsupported path command '{command}'")
    return out


def ellipse_path(cx, cy, rx, ry) -> list:
    k = KAPPA
    return [
        ["M", cx + rx, cy],
        ["C", cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry],
        ["C", cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy],
        ["C", cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry],
        ["C", cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy],
        ["Z"],
    ]


def rect_path(x, y, w, h, rx, ry) -> list:
    if rx <= 0 and ry <= 0:
        return [["M", x, y], ["L", x + w, y], ["L", x + w, y + h], ["L", x, y + h], ["Z"]]
    rx = min(rx if rx > 0 else ry, w / 2)
    ry = min(ry if ry > 0 else rx, h / 2)
    k = KAPPA
    return [
        ["M", x + rx, y],
        ["L", x + w - rx, y],
        ["C", x + w - rx + k * rx, y, x + w, y + ry - k * ry, x + w, y + ry],
        ["L", x + w, y + h - ry],
        ["C", x + w, y + h - ry + k * ry, x + w - rx + k * rx, y + h, x + w - rx, y + h],
        ["L", x + rx, y + h],
        ["C", x + rx - k * rx, y + h, x, y + h - ry + k * ry, x, y + h - ry],
        ["L", x, y + ry],
        ["C", x, y + ry - k * ry, x + rx - k * rx, y, x + rx, y],
        ["Z"],
    ]


def transform_path(path: list, m) -> list:
    out = []
    for command in path:
        coords = command[1:]
        moved = []
        for i in range(0, len(coords), 2):
            moved += apply(m, coords[i], coords[i + 1])
        out.append([command[0], *moved])
    return out


# MARK: SVG


@dataclass
class SvgResult:
    view_box: tuple
    parts: dict = field(default_factory=dict)
    pivots: dict = field(default_factory=dict)


def local(tag: str) -> str | None:
    """The element's name in the SVG namespace, or None for other namespaces (editor metadata)."""
    if tag.startswith("{"):
        ns, name = tag[1:].split("}", 1)
        return name if ns == SVG_NS else None
    return tag


def styles(el) -> dict:
    out = {}
    for key, value in el.attrib.items():
        if not key.startswith("{"):
            out[key] = value.strip()
    for item in (el.get("style") or "").split(";"):
        if ":" in item:
            key, value = item.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def normalize_hex(value: str) -> str | None:
    value = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.upper()
    return None


def num(value, default=0.0) -> float:
    if value is None:
        return default
    m = re.match(r"\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)", str(value))
    return float(m.group(1)) if m else default


def element_path(name: str, a: dict, where: str) -> list:
    if name == "path":
        return parse_path(a.get("d", ""), where)
    if name == "circle":
        r = num(a.get("r"))
        return ellipse_path(num(a.get("cx")), num(a.get("cy")), r, r)
    if name == "ellipse":
        return ellipse_path(num(a.get("cx")), num(a.get("cy")), num(a.get("rx")), num(a.get("ry")))
    if name == "rect":
        rx = num(a.get("rx"), -1)
        ry = num(a.get("ry"), -1)
        return rect_path(num(a.get("x")), num(a.get("y")), num(a.get("width")), num(a.get("height")), rx, ry)
    if name == "line":
        return [["M", num(a.get("x1")), num(a.get("y1"))], ["L", num(a.get("x2")), num(a.get("y2"))]]
    values = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", a.get("points", ""))]
    pts = list(zip(values[0::2], values[1::2]))
    if not pts:
        raise CompileError(f"{where}: {name} without points")
    path = [["M", *pts[0]]] + [["L", *p] for p in pts[1:]]
    return path + [["Z"]] if name == "polygon" else path


def parse_svg(text: str, filename: str, palette: dict, mode: str = "art") -> SvgResult:
    """Parses one SVG file. `mode` is "art" (palette roles, never `ink`) or "glyph" (black only, as `ink`)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise CompileError(f"{filename}: not valid XML ({error})") from None
    vb = [num(v) for v in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if v]
    if len(vb) != 4:
        raise CompileError(f"{filename}: the root <svg> needs a viewBox")
    result = SvgResult(view_box=(vb[2], vb[3]))

    def role(value: str, where: str):
        if value in (None, "", "none", "transparent"):
            return None
        if value.startswith("url("):
            raise CompileError(f"{where}: gradient and pattern fills aren't allowed in character art")
        hex_value = normalize_hex(value)
        if hex_value is None:
            raise CompileError(f"{where}: color '{value}' must be a hex value")
        if mode == "glyph":
            if hex_value != "#000000":
                raise CompileError(f"{where}: glyph art must be black (#000000), found {hex_value}")
            return "ink"
        name = palette.get(hex_value)
        if name is None or name == "ink":
            raise CompileError(f"{where}: {hex_value} isn't a character color in the palette")
        return name

    def walk(el, matrix, inherited, opacity, part):
        name = local(el.tag)
        if name is None or name in ("metadata", "title", "desc"):
            return
        where = f"{filename}: <{name}{' id=' + repr(el.get('id')) if el.get('id') else ''}>"
        if name in UNSUPPORTED:
            raise CompileError(f"{where}: {name} isn't supported in character art")
        a = styles(el)
        for attribute in ("filter", "mask", "clip-path"):
            if a.get(attribute) not in (None, "", "none"):
                raise CompileError(f"{where}: the {attribute} attribute isn't supported")
        for attribute in ("fill-opacity", "stroke-opacity"):
            if attribute in a and num(a[attribute], 1) != 1:
                raise CompileError(f"{where}: {attribute} isn't supported; use opacity")
        if name == "defs":
            for child in el.iter():
                child_name = local(child.tag)
                if child_name in UNSUPPORTED:
                    raise CompileError(f"{filename}: <{child_name}> isn't supported in character art")
            return
        if name in IGNORED:
            return
        matrix = multiply(matrix, parse_transform(a.get("transform", ""), where))
        inherited = {**inherited, **{k: a[k] for k in INHERITED if k in a}}
        opacity *= num(a.get("opacity"), 1)
        element_id = el.get("id") or ""
        if name == "circle" and element_id.startswith("pivot-"):
            result.pivots[element_id[len("pivot-") :]] = apply(matrix, num(a.get("cx")), num(a.get("cy")))
            return
        if name in ("svg", "g"):
            if name == "g" and part is None and element_id:
                part = element_id
                if part in result.parts:
                    raise CompileError(f"{where}: duplicate part '{part}'")
                result.parts[part] = []
            for child in el:
                walk(child, matrix, inherited, opacity, part)
            return
        if name not in DRAWABLE:
            raise CompileError(f"{where}: unsupported element")
        if part is None:
            raise CompileError(f"{where}: drawn outside a named group")
        fill = role(inherited.get("fill", "#000000"), where)
        stroke = role(inherited.get("stroke"), where)
        if fill is None and stroke is None:
            return
        scale = math.sqrt(abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]))
        result.parts[part].append(
            {
                "d": transform_path(element_path(name, a, where), matrix),
                "fill": fill,
                "stroke": stroke,
                "strokeWidth": num(inherited.get("stroke-width"), 1) * scale if stroke else 0,
                "opacity": opacity,
                "evenOdd": inherited.get("fill-rule") == "evenodd",
            }
        )

    walk(root, IDENTITY, {}, 1.0, None)
    return result


# MARK: Raster layers


def png_size(path: Path) -> tuple[int, int]:
    """A PNG's pixel width and height, read from its IHDR chunk."""
    head = path.read_bytes()[:24]
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        raise CompileError(f"{path.name}: not a PNG")
    return struct.unpack(">II", head[16:24])


def _numbers(value, count: int, where: str) -> list[float]:
    if not isinstance(value, list) or len(value) != count or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        raise CompileError(f"{where} must be {count} numbers")
    return [float(v) for v in value]


def raster_parts(folder: Path, rig: dict, filename: str) -> tuple[tuple, dict, dict, dict]:
    """The view box, pivots, parts and layer files of a raster skin, from its rig and `layers/` folder."""
    skin = folder.name
    for key in ("viewBox", "pivots", "layers"):
        if key not in rig:
            raise CompileError(f"{filename}: a raster rig needs '{key}'")
    view_box = tuple(_numbers(rig["viewBox"], 2, f"{filename}: viewBox"))
    if min(view_box) <= 0:
        raise CompileError(f"{filename}: viewBox must be positive")
    pivots = {name: tuple(_numbers(point, 2, f"{filename}: pivot '{name}'")) for name, point in rig["pivots"].items()}
    if not rig["layers"]:
        raise CompileError(f"{filename}: layers is empty")
    parts, files = {}, {}
    for part, layer in rig["layers"].items():
        where = f"{filename}: layer '{part}'"
        if not isinstance(layer, dict) or "frame" not in layer:
            raise CompileError(f"{where} needs a frame")
        frame = _numbers(layer["frame"], 4, f"{where}: frame")
        if frame[2] <= 0 or frame[3] <= 0:
            raise CompileError(f"{where}: the frame's width and height must be positive")
        files[part] = {}
        for scale in SCALES:
            png = folder / "layers" / f"{part}@{scale}x.png"
            if not png.exists():
                if scale == SCALES[0]:
                    raise CompileError(f"{where}: layers/{png.name} is missing")
                continue
            width, height = png_size(png)
            expected = (frame[2] * scale, frame[3] * scale)
            if abs(width - expected[0]) > 1e-6 or abs(height - expected[1]) > 1e-6:
                raise CompileError(
                    f"layers/{png.name}: {width}×{height} pixels, but the frame at @{scale}x needs "
                    f"{_number(expected[0])}×{_number(expected[1])}"
                )
            files[part][scale] = png
        parts[part] = {"image": f"{skin}.{part}", "frame": frame}
    for png in sorted((folder / "layers").glob("*.png")):
        match = re.fullmatch(r"(.+)@(\d)x\.png", png.name)
        if not match or match.group(1) not in files or int(match.group(2)) not in SCALES:
            raise CompileError(f"layers/{png.name}: not a layer in {filename} (name it <part>@2x.png or <part>@3x.png)")
    return view_box, pivots, parts, files


# MARK: Rig


def validate_rig(rig: dict, parts: set, pivots: set, frames: set, filename: str, moods: list[str] = MOODS) -> dict:
    """Checks a rig against the compiled parts, pivots and glyph frames and returns it with every default filled in."""

    def fail(message):
        raise CompileError(f"{filename}: {message}")

    def need(key, container=rig, where="rig"):
        if key not in container:
            fail(f"{where} is missing '{key}'")
        return container[key]

    def part(name, where):
        if name not in parts:
            fail(f"{where} names unknown part '{name}'")
        return name

    def group(key):
        g = need(key)
        if need("pivot", g, key) not in pivots:
            fail(f"{key} names unknown pivot '{g['pivot']}'")
        for p in need("parts", g, key):
            part(p, key)
        return {"pivot": g["pivot"], "parts": list(g["parts"]), "follow": float(g.get("follow", 1))}

    def extras(m, key, sugar, where):
        """`headExtras` / `overlays`, or the boolean sugar for them (`cheeks: true`, `zz: true`)."""
        if sugar in m:
            if key in m:
                fail(f"{where} has both '{sugar}' and '{key}'; keep one")
            return [sugar] if m[sugar] else []
        return list(m.get(key, []))

    period = float(need("period"))
    if period <= 0:
        fail("period must be positive")
    out = {"period": period, "stillTime": float(need("stillTime"))}
    out["always"] = [part(p, "always") for p in need("always")]
    for key in ("head", "stem", "pulse"):
        out[key] = group(key)
    blink = need("blink")
    intervals = [float(v) for v in need("intervals", blink, "blink")]
    if not intervals or min(intervals) <= 0:
        fail("blink intervals must be positive")
    out["blink"] = {
        "duration": float(need("duration", blink, "blink")),
        "intervals": intervals,
        "closedEyes": part(need("closedEyes", blink, "blink"), "blink"),
    }
    rig_moods = need("moods")
    for mood in moods:
        if mood not in rig_moods:
            fail(f"mood '{mood}' is missing")
    for mood in rig_moods:
        if mood not in moods:
            fail(f"unknown mood '{mood}'")
    out["moods"] = {}
    for mood in moods:
        m = rig_moods[mood]
        where = f"mood '{mood}'"
        mouths = need("mouth", m, where)
        if not mouths:
            fail(f"{where} needs at least one mouth")
        entry = {
            "eyes": part(need("eyes", m, where), where),
            "mouth": [part(p, where) for p in mouths],
            "mouthRate": float(m.get("mouthRate", 0)),
            "headExtras": [part(p, where) for p in extras(m, "headExtras", "cheeks", where)],
            "overlays": [part(p, where) for p in extras(m, "overlays", "zz", where)],
            "blink": bool(m.get("blink", False)),
            "droop": float(m.get("droop", 0)),
            "sink": float(m.get("sink", 0)),
            "sway": float(m.get("sway", 0)),
            "swayPeriod": float(m.get("swayPeriod", period)),
            "pulse": float(m.get("pulse", 0)),
        }
        if len(entry["mouth"]) > 1 and entry["mouthRate"] <= 0:
            fail(f"{where} alternates mouths, so it needs a positive mouthRate")
        if "restMouth" in m:  # the closed mouth between words when a clip's envelope drives it; omitted when absent
            entry["restMouth"] = part(m["restMouth"], where)
        out["moods"][mood] = entry
    glyph = need("glyph")
    out["glyph"] = {}
    for mood in moods:
        ids = glyph.get(mood) or []
        if not ids:
            fail(f"mood '{mood}' has no glyph frames")
        for frame in ids:
            if frame not in frames:
                fail(f"mood '{mood}' names unknown glyph frame '{frame}'")
        out["glyph"][mood] = list(ids)
    bubble = need("bubble")
    if need("pivot", bubble, "bubble") not in pivots:
        fail(f"bubble names unknown pivot '{bubble['pivot']}'")
    tuck = float(need("tuck", bubble, "bubble"))
    if not 0 <= tuck < 1:
        fail("bubble tuck is a fraction of the width, from 0 up to 1")
    out["bubble"] = {"pivot": bubble["pivot"], "tuck": tuck}
    return out


# MARK: Skins


@dataclass
class Compiled:
    """A skin's data (what the JSON holds) and, for a raster skin, its layer PNGs by part and scale."""

    data: dict
    layers: dict = field(default_factory=dict)


def compile_skin(folder: Path, palette: dict, mode: str = "vector", moods: list[str] = MOODS) -> Compiled:
    skin = folder.name
    rig_path = folder / f"{skin}.rig.json"
    if not rig_path.exists():
        raise CompileError(f"{folder}: {rig_path.name} is missing")
    try:
        rig_data = json.loads(rig_path.read_text())
    except json.JSONDecodeError as error:
        raise CompileError(f"{rig_path.name}: not valid JSON ({error})") from None
    glyph_path = folder / "glyph.svg"
    if not glyph_path.exists():
        raise CompileError(f"{folder}: glyph.svg is missing")
    glyph = parse_svg(glyph_path.read_text(), glyph_path.name, palette, mode="glyph")
    if glyph.view_box[0] != glyph.view_box[1]:
        raise CompileError("glyph.svg: the glyph viewBox must be square")
    art_svgs = [p for p in sorted(folder.glob("*.svg")) if p.name != "glyph.svg"]
    layers: dict = {}
    if mode == "raster":
        if art_svgs:
            raise CompileError(f"{art_svgs[0].name}: a raster skin draws its parts from layers/, not SVGs")
        view_box, pivots, parts, layers = raster_parts(folder, rig_data, rig_path.name)
    else:
        for key in ("layers", "viewBox", "pivots"):
            if key in rig_data:
                raise CompileError(f"{rig_path.name}: '{key}' belongs to a raster rig (--mode raster)")
        parts, pivots, view_box = {}, {}, None
        for path in art_svgs:
            result = parse_svg(path.read_text(), path.name, palette, mode="art")
            if view_box is None:
                view_box = result.view_box
            elif result.view_box != view_box:
                raise CompileError(f"{path.name}: viewBox {result.view_box} differs from {view_box}")
            for name, shapes in result.parts.items():
                if name in parts:
                    raise CompileError(f"{path.name}: duplicate part '{name}'")
                parts[name] = shapes
            for name, point in result.pivots.items():
                if name in pivots:
                    raise CompileError(f"{path.name}: duplicate pivot '{name}'")
                pivots[name] = point
        if view_box is None:
            raise CompileError(f"{folder}: no art SVGs")
    rig = validate_rig(rig_data, set(parts), set(pivots), set(glyph.parts), rig_path.name, moods)
    data = {
        "skin": skin,
        "viewBox": list(view_box),
        "pivots": {name: list(point) for name, point in pivots.items()},
        "parts": parts,
        "rig": rig,
        "glyph": {"size": glyph.view_box[0], "frames": glyph.parts},
    }
    return Compiled(data, layers)


def load_palette(path: Path, required: bool) -> dict:
    if not path.exists():
        if required:
            raise CompileError(f"{path}: the palette is missing")
        return {}
    return {normalize_hex(k) or k: v for k, v in json.loads(path.read_text()).items()}


# MARK: Output


def _number(value):
    value = round(float(value), 3)
    if value == 0:
        return 0
    return int(value) if value.is_integer() else value


def _plain(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return [_plain(v) for v in value]


def _inline(value) -> bool:
    return isinstance(value, list) and all(not isinstance(v, dict) and (not isinstance(v, list) or _inline(v)) for v in value)


def _render(value, indent: int) -> str:
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}{json.dumps(k)}: {_render(value[k], indent + 1)}" for k in sorted(value)]
        return "{\n" + ",\n".join(items) + "\n" + "  " * indent + "}"
    if isinstance(value, list) and not _inline(value):
        items = [f"{pad}{_render(v, indent + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + "\n" + "  " * indent + "]"
    return json.dumps(value, ensure_ascii=False)


def dump(data) -> str:
    """Stable JSON: sorted keys, 2-space indent, numbers to 3 decimals, paths and scalar lists on one line."""
    return _render(_plain(data), 0) + "\n"


# MARK: Swift


def swift_name(skin: str) -> str:
    """`fixture-vector` → `fixtureVector`: the skin as a Swift identifier."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", skin) if w]
    if not words or words[0][0].isdigit():
        raise CompileError(f"skin '{skin}' doesn't make a Swift identifier")
    return words[0][0].lower() + words[0][1:] + "".join(w[0].upper() + w[1:] for w in words[1:])


def _swift_number(value) -> str:
    return str(_number(value))


def _swift_point(x, y) -> str:
    return f"Point({_swift_number(x)}, {_swift_number(y)})"


def _swift_shape(shape: dict) -> str:
    commands = []
    for c in shape["d"]:
        if c[0] == "M":
            commands.append(f".move({_swift_point(c[1], c[2])})")
        elif c[0] == "L":
            commands.append(f".line({_swift_point(c[1], c[2])})")
        elif c[0] == "C":
            commands.append(f".cubic({_swift_point(c[1], c[2])}, {_swift_point(c[3], c[4])}, {_swift_point(c[5], c[6])})")
        else:
            commands.append(".close")
    args = [f"[{', '.join(commands)}]"]
    if shape["fill"]:
        args.append(f"fill: {json.dumps(shape['fill'])}")
    if shape["stroke"]:
        args.append(f"stroke: {json.dumps(shape['stroke'])}")
        args.append(f"strokeWidth: {_swift_number(shape['strokeWidth'])}")
    if _number(shape["opacity"]) != 1:
        args.append(f"opacity: {_swift_number(shape['opacity'])}")
    if shape["evenOdd"]:
        args.append("evenOdd: true")
    return f"PathShape({', '.join(args)})"


def _swift_strings(values) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def swift_source(data: dict, module: str | None, moods: list[str] = MOODS) -> str:
    """`CharacterArt.<skin>` for the Swift core, from the same (rounded) data as the JSON."""
    data = json.loads(dump(data))
    skin, rig = data["skin"], data["rig"]
    name = swift_name(skin)
    part_names = sorted(data["parts"])
    frame_names = sorted(data["glyph"]["frames"])

    def group(g):
        return f'RigGroup(pivot: {json.dumps(g["pivot"])}, parts: {_swift_strings(g["parts"])}, follow: {_swift_number(g["follow"])})'

    def content(i, part):
        value = data["parts"][part]
        if isinstance(value, dict):
            x, y, w, h = (_swift_number(v) for v in value["frame"])
            return f'.image(asset: {json.dumps(value["image"])}, frame: Rect({x}, {y}, {w}, {h}))'
        return f".shapes({name}Part{i})"

    mood_lines = []
    for mood in moods:
        m = rig["moods"][mood]
        mood_lines.append(
            f"                .{mood}: MoodRig(eyes: {json.dumps(m['eyes'])}, mouth: {_swift_strings(m['mouth'])}, "
            f"mouthRate: {_swift_number(m['mouthRate'])}, headExtras: {_swift_strings(m['headExtras'])}, "
            f"overlays: {_swift_strings(m['overlays'])}, blink: {str(m['blink']).lower()}, "
            f"droop: {_swift_number(m['droop'])}, sink: {_swift_number(m['sink'])}, "
            f"sway: {_swift_number(m['sway'])}, swayPeriod: {_swift_number(m['swayPeriod'])}, pulse: {_swift_number(m['pulse'])}"
            + (f", restMouth: {json.dumps(m['restMouth'])}" if "restMouth" in m else "")
            + "),"
        )
    glyph = ", ".join(f".{mood}: {_swift_strings(rig['glyph'][mood])}" for mood in moods)
    pivots = ", ".join(f'{json.dumps(k)}: {_swift_point(*v)}' for k, v in sorted(data["pivots"].items()))
    bubble = rig["bubble"]
    lines = [
        f"// Generated by tools/character.py from the {skin} art. Do not edit.",
        "// swift-format-ignore-file",
        "",
        *([f"import {module}", ""] if module else []),
        "extension CharacterArt {",
        f"    /// The `{skin}` skin, compiled.",
        f"    public static let {name} = CharacterArt(",
        f"        skin: {json.dumps(skin)},",
        f"        viewBox: Size({_swift_number(data['viewBox'][0])}, {_swift_number(data['viewBox'][1])}),",
        f"        pivots: [{pivots}],",
        "        parts: [",
        *[f"            {json.dumps(part)}: {content(i, part)}," for i, part in enumerate(part_names)],
        "        ],",
        "        rig: Rig(",
        f"            period: {_swift_number(rig['period'])}, stillTime: {_swift_number(rig['stillTime'])},",
        f"            always: {_swift_strings(rig['always'])},",
        f"            head: {group(rig['head'])},",
        f"            stem: {group(rig['stem'])},",
        f"            pulse: {group(rig['pulse'])},",
        f"            blink: Blink(duration: {_swift_number(rig['blink']['duration'])}, intervals: "
        f"[{', '.join(_swift_number(v) for v in rig['blink']['intervals'])}], closedEyes: {json.dumps(rig['blink']['closedEyes'])}),",
        "            moods: [",
        *mood_lines,
        "            ],",
        f"            glyph: [{glyph}],",
        f"            bubble: BubbleAnchor(pivot: {json.dumps(bubble['pivot'])}, tuck: {_swift_number(bubble['tuck'])})),",
        f"        glyphSize: {_swift_number(data['glyph']['size'])},",
        "        glyphFrames: [",
        *[f"            {json.dumps(frame)}: {name}Glyph{i}," for i, frame in enumerate(frame_names)],
        "        ])",
    ]
    for prefix, names, source in (("Part", part_names, data["parts"]), ("Glyph", frame_names, data["glyph"]["frames"])):
        for i, part in enumerate(names):
            if isinstance(source[part], dict):
                continue
            lines += ["", f"    /// {part}", f"    private static let {name}{prefix}{i}: [PathShape] = ["]
            lines += [f"        {_swift_shape(shape)}," for shape in source[part]]
            lines.append("    ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


# MARK: Assets


def imageset_contents(asset: str, scales) -> str:
    """An Xcode imageset's Contents.json for one layer."""
    images = [{"filename": f"{asset}@{scale}x.png", "idiom": "universal", "scale": f"{scale}x"} for scale in scales]
    return json.dumps({"images": images, "info": {"author": "xcode", "version": 1}}, indent=2, sort_keys=True) + "\n"


def outputs(args) -> dict[Path, bytes | str]:
    """Every file one run writes, by path: text as text (`companion.write_outputs` lets a CRLF checkout match), the PNGs
    as bytes."""
    palette = load_palette(args.palette or args.design / "palette.json", required=args.mode == "vector")
    compiled = compile_skin(args.design / args.skin, palette, args.mode, args.moods)
    files: dict[Path, bytes | str] = {args.out: dump(compiled.data)}
    if args.swift_out:
        files[args.swift_out] = swift_source(compiled.data, args.module, args.moods)
    for part, scales in sorted(compiled.layers.items()):
        asset = compiled.data["parts"][part]["image"]
        if args.assets:
            folder = args.assets / f"{asset}.imageset"
            files[folder / "Contents.json"] = imageset_contents(asset, sorted(scales))
            for scale, png in scales.items():
                files[folder / f"{asset}@{scale}x.png"] = png.read_bytes()
        if args.windows_assets:
            for scale, png in scales.items():
                files[args.windows_assets / f"{asset}.scale-{scale * 100}.png"] = png.read_bytes()
    return files


# MARK: Review sheet

PAPER, NIGHT, BAR_LIGHT, BAR_DARK = "#FFF8E7", "#1F1A14", "#ECECEC", "#2A2A2A"
INK_SOFT = {"paper": "#7A6A55", "night": "#C9BCA5", "busy": "#3A2F24"}


def _d(path: list) -> str:
    return " ".join(c[0] + " ".join(f"{_number(v)}" for v in c[1:]) for c in path)


def _shape_svg(shape: dict, colors: dict) -> str:
    fill = colors.get(shape["fill"], "#FF00FF") if shape["fill"] else "none"
    attrs = [f'd="{_d(shape["d"])}"', f'fill="{fill}"']
    if shape["stroke"]:
        attrs += [f'stroke="{colors.get(shape["stroke"], "#FF00FF")}"', f'stroke-width="{_number(shape["strokeWidth"])}"',
                  'stroke-linecap="round"', 'stroke-linejoin="round"']
    if shape["opacity"] != 1:
        attrs.append(f'opacity="{_number(shape["opacity"])}"')
    if shape["evenOdd"]:
        attrs.append('fill-rule="evenodd"')
    return f"<path {' '.join(attrs)}/>"


def _part_svg(content, colors: dict, images: dict) -> str:
    if isinstance(content, dict):
        x, y, w, h = (_number(v) for v in content["frame"])
        return f'<image href="{images[content["image"]]}" x="{x}" y="{y}" width="{w}" height="{h}"/>'
    return "".join(_shape_svg(shape, colors) for shape in content)


def _busy(x: float, y: float, w: float, h: float) -> str:
    """A loud, deterministic multicolor pattern standing in for a busy wallpaper."""
    colors = ["#E4572E", "#29335C", "#F3A712", "#A8C686", "#669BBC", "#C1292E", "#F1D302", "#020100", "#FDFFFC", "#8E7DBE"]
    seed, out = 7, [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#669BBC"/>']
    for _ in range(40):
        seed = (seed * 1103515245 + 12345) % 2**31
        cx, cy = x + seed % int(w), y + (seed // 7) % int(h)
        r = 6 + (seed // 13) % 22
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colors[(seed // 17) % len(colors)]}"/>')
    return "".join(out)


def _pose_svg(data: dict, mood: str, mouth: int, colors: dict, images: dict) -> str:
    rig, pivots, parts = data["rig"], data["pivots"], data["parts"]
    m = rig["moods"][mood]
    head, stem = rig["head"], rig["stem"]
    nx, ny = pivots[head["pivot"]]
    rx, ry = pivots[stem["pivot"]]
    head_t = f'translate(0 {_number(m["sink"])}) rotate({_number(m["droop"])} {_number(nx)} {_number(ny)})'
    stem_t = f'rotate({_number(m["droop"] * stem["follow"])} {_number(rx)} {_number(ry)})'

    def group(name, transform=None):
        body = _part_svg(parts[name], colors, images)
        return f'<g transform="{transform}">{body}</g>' if transform else body

    out = []
    for name in rig["always"]:
        out.append(group(name, head_t if name in head["parts"] else stem_t if name in stem["parts"] else None))
    features = m["headExtras"] + [m["eyes"], m["mouth"][mouth % len(m["mouth"])]]
    out += [group(name, head_t) for name in features]
    out += [group(name) for name in m["overlays"]]
    return "".join(out)


def preview_svg(compiled: Compiled, palette: dict, moods: list[str] = MOODS) -> str:
    """The review sheet: every mood on paper, night and a busy pattern, then the glyph frames at 4x and 1x."""
    data = compiled.data
    colors = {}
    for hex_value, role in palette.items():
        colors.setdefault(role, normalize_hex(hex_value) or hex_value)
    images = {
        data["parts"][part]["image"]: "data:image/png;base64," + base64.b64encode(scales[min(scales)].read_bytes()).decode()
        for part, scales in compiled.layers.items()
    }
    vw, vh = data["viewBox"]
    columns = []
    for mood in moods:
        count = len(data["rig"]["moods"][mood]["mouth"]) if mood == "talking" else 1
        columns += [(f"{mood}-{i + 1}" if count > 1 else mood, mood, i) for i in range(count)]
    cell, gap, label = 150, 12, 22
    scale = (cell - 16) / max(vw, vh)
    out = []
    tiles = 0
    for row, background in enumerate(("paper", "night", "busy")):
        y = label + row * (cell + gap)
        for col, (name, mood, mouth) in enumerate(columns):
            x = gap + col * (cell + gap)
            fill = PAPER if background == "paper" else NIGHT
            if background == "busy":
                out.append(f'<clipPath id="busy-{tiles}"><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="12"/></clipPath>'
                           f'<g clip-path="url(#busy-{tiles})">{_busy(x, y, cell, cell)}</g>')
                tiles += 1
            else:
                out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="12" fill="{fill}"/>')
            palette_colors = {**colors, "inkSoft": INK_SOFT[background]}
            ox, oy = x + (cell - vw * scale) / 2, y + (cell - vh * scale) / 2
            out.append(f'<g id="pose-{name}-{background}" transform="translate({_number(ox)} {_number(oy)}) scale({_number(scale)})">'
                       f'{_pose_svg(data, mood, mouth, palette_colors, images)}</g>')
            if row == 0:
                out.append(f'<text x="{x + cell / 2}" y="{label - 6}" font-family="Helvetica" font-size="13" text-anchor="middle">{name}</text>')
    y = label + 3 * (cell + gap) + 10
    size = data["glyph"]["size"]
    frames = []
    for mood in moods:
        frames += [f for f in data["rig"]["glyph"][mood] if f not in frames]
    frames += sorted(f for f in data["glyph"]["frames"] if f not in frames)
    for i, frame in enumerate(frames):
        x = gap + i * (4 * size + 24)
        out.append(f'<rect x="{x}" y="{y}" width="{4 * size + 12}" height="{4 * size + 12}" rx="8" fill="{BAR_LIGHT}"/>')
        body = "".join(_shape_svg(s, {"ink": "#000000"}) for s in data["glyph"]["frames"][frame])
        out.append(f'<g id="glyph-{frame}" transform="translate({x + 6} {y + 6}) scale(4)">{body}</g>')
        out.append(f'<text x="{x + 2 * size + 6}" y="{y + 4 * size + 30}" font-family="Helvetica" font-size="12" text-anchor="middle">{frame}</text>')
    y += 4 * size + 44
    for j, (bar, ink) in enumerate(((BAR_LIGHT, "#1D1D1F"), (BAR_DARK, "#F5F5F7"))):
        bx = gap + j * 420
        out.append(f'<rect x="{bx}" y="{y}" width="400" height="24" rx="5" fill="{bar}"/>')
        for i, frame in enumerate(frames):
            body = "".join(_shape_svg(s, {"ink": ink}) for s in data["glyph"]["frames"][frame])
            out.append(f'<g id="glyph-{frame}-1x-{j}" transform="translate({bx + 14 + i * 34} {y + 3})">{body}</g>')
    width = gap + len(columns) * (cell + gap)
    height = y + 40
    side = max(width, height)
    # Square (qlmanage renders squares) with the content centered vertically, so a centered crop keeps all of it.
    top = (side - height) / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" viewBox="0 0 {side} {side}" '
            f'data-content-height="{height}">'
            f'<rect width="{side}" height="{side}" fill="#FFFFFF"/><g transform="translate(0 {_number(top)})">{"".join(out)}</g></svg>\n')


# MARK: Command line


def _moods(text: str) -> list[str]:
    moods = [m.strip() for m in text.split(",") if m.strip()]
    if not moods or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", m) for m in moods):
        raise argparse.ArgumentTypeError("--moods is a comma-separated list of Swift identifiers")
    return moods


def _common(parser: argparse.ArgumentParser):
    parser.add_argument("--design", type=Path, required=True, help="the folder holding the skin folders (and palette.json)")
    parser.add_argument("--skin", required=True, help="the skin's folder name under --design")
    parser.add_argument("--mode", choices=("vector", "raster"), default="vector", help="parts from SVGs (vector) or layer PNGs (raster)")
    parser.add_argument("--palette", type=Path, help="hex → paint role (default: <design>/palette.json)")
    parser.add_argument("--moods", type=_moods, default=MOODS, help=f"the Mood cases, in order (default: {','.join(MOODS)})")


def preview(argv) -> int:
    parser = argparse.ArgumentParser(prog="character.py preview", description="Render a skin's review sheet.")
    parser.add_argument("out", type=Path)
    _common(parser)
    parser.add_argument("--png", action="store_true", help="also render a PNG next to it with qlmanage (macOS)")
    args = parser.parse_args(argv)
    try:
        palette = load_palette(args.palette or args.design / "palette.json", required=args.mode == "vector")
        compiled = compile_skin(args.design / args.skin, palette, args.mode, args.moods)
    except CompileError as error:
        print(f"character.py: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(preview_svg(compiled, palette, args.moods))
    print(f"wrote {args.out}")
    if args.png:
        import subprocess

        rendered = args.out.parent / (args.out.name + ".png")
        rendered.unlink(missing_ok=True)
        subprocess.run(["qlmanage", "-t", "-s", "2400", "-o", str(args.out.parent), str(args.out)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
        if not rendered.exists():
            print("qlmanage didn't render a PNG", file=sys.stderr)
            return 1
        png = args.out.with_suffix(".png")
        rendered.replace(png)
        # qlmanage renders a square; crop it to the sheet's content (sips ships with macOS and crops around the center).
        sheet = args.out.read_text()
        side = float(re.search(r'width="([\d.]+)"', sheet).group(1))
        content = float(re.search(r'data-content-height="([\d.]+)"', sheet).group(1))
        size = subprocess.run(["sips", "-g", "pixelWidth", str(png)], capture_output=True, text=True).stdout
        pixels = int(re.search(r"pixelWidth: (\d+)", size).group(1))
        keep = int(pixels * content / side)
        subprocess.run(["sips", "--cropToHeightWidth", str(keep), str(pixels), str(png)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        print(f"wrote {png}")
    return 0


def app_runs(config: companion.Config, skins: list[str] | None, check: bool) -> list[argparse.Namespace]:
    """One `outputs` run per skin of the app, from companion.json's `characters` section."""
    design = config.require_path("characters.design")
    mode = config.get("characters.mode", "vector")
    found = sorted(p.name for p in design.iterdir() if (p / f"{p.name}.rig.json").exists()) if design.is_dir() else []
    for skin in skins or []:
        if skin not in found:
            raise CompileError(f"no skin '{skin}' in {design} (a folder with {skin}.rig.json)")
    if not found:
        raise CompileError(f"no skins in {design}")
    characters = config.require_path("conformance.dir") / "characters"
    swift_out = config.path("characters.swiftOut")
    runs = []
    for skin in skins or found:
        name = swift_name(skin)
        runs.append(argparse.Namespace(
            design=design, skin=skin, mode=mode, palette=config.path("characters.paletteFile"),
            moods=config.get("characters.moods", MOODS), out=characters / f"{skin}.json",
            swift_out=swift_out / f"{name[0].upper()}{name[1:]}Art.swift" if swift_out else None, module="LachingCompanion",
            assets=config.path("characters.assets") if mode == "raster" else None,
            windows_assets=config.path("characters.windowsAssets") if mode == "raster" else None, check=check))
    return runs


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "preview":
        return preview(argv[1:])
    if "--design" not in argv and not any(a.startswith("--design=") for a in argv):
        parser = argparse.ArgumentParser(description="Compile the app's skins, as its companion.json says.")
        parser.add_argument("--skin", action="append", help="only this skin (repeatable; default: every skin folder)")
        parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if an output is stale")
        args = parser.parse_args(argv)
        try:
            runs = app_runs(companion.load(), args.skin, args.check)
        except CompileError as error:
            print(f"character.py: {error}", file=sys.stderr)
            return 2
        return max(write(run) for run in runs)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    _common(parser)
    parser.add_argument("--out", type=Path, required=True, help="the art as JSON (conformance/…/characters/<skin>.json)")
    parser.add_argument("--swift-out", type=Path, help="the art as a Swift source file")
    parser.add_argument("--module", default="LachingCompanion", help="the module the Swift file imports ('' for none)")
    parser.add_argument("--assets", type=Path, help="raster: an asset catalog folder to get an imageset per layer")
    parser.add_argument("--windows-assets", type=Path, help="raster: a folder to get the scale-200/300 PNGs")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if an output is stale")
    args = parser.parse_args(argv)
    if (args.assets or args.windows_assets) and args.mode != "raster":
        parser.error("--assets and --windows-assets need --mode raster")
    return write(args)


def write(args) -> int:
    """Writes (or with `check`, compares) every output of one run; 1 when something was stale, 2 on a compile error."""
    try:
        files = outputs(args)
    except CompileError as error:
        print(f"character.py: {error}", file=sys.stderr)
        return 2
    changed = companion.write_outputs(files, args.check)
    for path in changed:
        if args.check:
            print(f"{path.as_posix()} is stale: run tools/character.py and commit the result", file=sys.stderr)
        else:
            print(f"wrote {path.as_posix()}")
    return 1 if changed and args.check else 0


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
