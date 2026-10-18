#!/usr/bin/env python3
"""Renders a companion's Windows app images from its app icon (`windows.assets` in companion.json).

The MSIX's logos (Square44x44Logo at scale 100 and 200 and at the taskbar's target sizes, plain and unplated,
Square150x150Logo, Wide310x150Logo, StoreLogo, SplashScreen) and AppIcon.ico (16 to 256, PNG entries), from `source`, a
square PNG such as the Mac's 1024-pixel app icon; `devSource` adds `Dev/AppIcon.ico` for the Debug build, from the
DEV-banded icon. The icon is trimmed to what it draws (a Mac icon leaves a margin around its shape), then scaled and
centred, the same layout as mybackhurts' assets. Standard library only, so it runs wherever the icon is rendered:

    python3 laching/tools/windows/assets.py            # write the images into `out`
    python3 laching/tools/windows/assets.py --check    # exit 1 when a committed image isn't what it would write

companion.json: `"windows": {"assets": {"source": "...png", "devSource": "...png", "out": "windows/App/Assets"}}`.
Reads 8-bit RGB or RGBA PNGs, not interlaced; downsamples by halving, then by area, in premultiplied alpha.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import companion  # noqa: E402

ICO_SIZES = [16, 24, 32, 48, 64, 256]
TARGET_SIZES = [16, 24, 32, 48, 256]


class Image:
    """Premultiplied RGBA as floats from 0 to 255, row by row."""

    def __init__(self, width: int, height: int, pixels: array):
        self.width, self.height, self.pixels = width, height, pixels

    @staticmethod
    def blank(width: int, height: int) -> Image:
        return Image(width, height, array("f", bytes(4 * 4 * width * height)))


# PNG


def read_png(path: Path) -> Image:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path}: not a PNG")
    pos, idat, header = 8, bytearray(), None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None:
        raise SystemExit(f"{path}: no IHDR")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace:
        raise SystemExit(f"{path}: needs 8-bit RGB or RGBA, not interlaced")
    channels = 4 if color == 6 else 3
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows, previous, offset = [], bytearray(stride), 0
    for _ in range(height):
        kind = raw[offset]
        line = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += 1 + stride
        unfilter(kind, line, previous, channels)
        rows.append(line)
        previous = line
    pixels = array("f", bytes(4 * 4 * width * height))
    i = 0
    for line in rows:
        for x in range(width):
            r, g, b = line[x * channels], line[x * channels + 1], line[x * channels + 2]
            a = line[x * channels + 3] if channels == 4 else 255
            f = a / 255
            pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3] = r * f, g * f, b * f, a
            i += 4
    return Image(width, height, pixels)


def unfilter(kind: int, line: bytearray, previous: bytearray, bpp: int) -> None:
    for x in range(len(line)):
        left = line[x - bpp] if x >= bpp else 0
        up = previous[x]
        if kind == 1:
            line[x] = (line[x] + left) & 0xFF
        elif kind == 2:
            line[x] = (line[x] + up) & 0xFF
        elif kind == 3:
            line[x] = (line[x] + (left + up) // 2) & 0xFF
        elif kind == 4:
            corner = previous[x - bpp] if x >= bpp else 0
            p = left + up - corner
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
            line[x] = (line[x] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 0xFF


def png_bytes(image: Image) -> bytes:
    """The image as an RGBA PNG, unpremultiplied, every row filtered None (deterministic, so --check compares bytes)."""
    raw = bytearray()
    p = image.pixels
    for y in range(image.height):
        raw.append(0)
        for x in range(image.width):
            i = 4 * (y * image.width + x)
            a = p[i + 3]
            if a < 0.5:
                raw += b"\0\0\0\0"
                continue
            k = 255 / a
            raw += bytes(min(255, max(0, round(p[i + c] * k))) for c in range(3))
            raw.append(min(255, round(a)))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(pngs: list[tuple[int, bytes]]) -> bytes:
    """An .ico: a directory of PNG images (Windows Vista and later read PNG entries at every size)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, bodies = b"", b""
    for size, png in pngs:
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        bodies += png
        offset += len(png)
    return header + entries + bodies


# Geometry


def trim(image: Image, threshold: float = 8) -> Image:
    """The smallest square around what the image draws (alpha above `threshold`), centred on its content."""
    w, h, p = image.width, image.height, image.pixels
    xs = [x for x in range(w) if any(p[4 * (y * w + x) + 3] > threshold for y in range(0, h, 2))]
    ys = [y for y in range(h) if any(p[4 * (y * w + x) + 3] > threshold for x in range(0, w, 2))]
    if not xs or not ys:
        return image
    left, right, top, bottom = xs[0], xs[-1] + 1, ys[0], ys[-1] + 1
    side = max(right - left, bottom - top)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    x0, y0 = cx - side // 2, cy - side // 2
    out = Image.blank(side, side)
    for y in range(side):
        sy = y0 + y
        if not 0 <= sy < h:
            continue
        for x in range(side):
            sx = x0 + x
            if 0 <= sx < w:
                s, d = 4 * (sy * w + sx), 4 * (y * side + x)
                out.pixels[d : d + 4] = p[s : s + 4]
    return out


def halve(image: Image) -> Image:
    w, h, p = image.width // 2, image.height // 2, image.pixels
    out = Image.blank(w, h)
    sw = image.width
    for y in range(h):
        for x in range(w):
            a, b = 4 * (2 * y * sw + 2 * x), 4 * ((2 * y + 1) * sw + 2 * x)
            d = 4 * (y * w + x)
            for c in range(4):
                out.pixels[d + c] = (p[a + c] + p[a + 4 + c] + p[b + c] + p[b + 4 + c]) / 4
    return out


def area(image: Image, width: int, height: int) -> Image:
    """Area-average resampling, horizontal then vertical."""

    def weights(source: int, target: int) -> list[list[tuple[int, float]]]:
        scale = source / target
        spans = []
        for i in range(target):
            start, end = i * scale, (i + 1) * scale
            span, s = [], int(start)
            while s < end and s < source:
                cover = min(end, s + 1) - max(start, s)
                if cover > 0:
                    span.append((s, cover / scale))
                s += 1
            spans.append(span)
        return spans

    sw, sh, p = image.width, image.height, image.pixels
    across = weights(sw, width)
    middle = array("f", bytes(4 * 4 * width * sh))
    for y in range(sh):
        for x, span in enumerate(across):
            d = 4 * (y * width + x)
            for c in range(4):
                middle[d + c] = sum(p[4 * (y * sw + s) + c] * f for s, f in span)
    down = weights(sh, height)
    out = Image.blank(width, height)
    for y, span in enumerate(down):
        for x in range(width):
            d = 4 * (y * width + x)
            for c in range(4):
                out.pixels[d + c] = sum(middle[4 * (s * width + x) + c] * f for s, f in span)
    return out


def scaled(image: Image, size: int, cache: dict[int, Image]) -> Image:
    if size not in cache:
        source = image
        while source.width >= 2 * size and source.width >= 2:
            source = halve(source)
        cache[size] = source if source.width == size else area(source, size, size)
    return cache[size]


def place(icon: Image, width: int, height: int, size: int, cache: dict[int, Image]) -> Image:
    """The icon, `size` pixels square, centred on a transparent `width` × `height` canvas."""
    small = scaled(icon, size, cache)
    if width == size and height == size:
        return small
    out = Image.blank(width, height)
    x0, y0 = (width - size) // 2, (height - size) // 2
    for y in range(size):
        d, s = 4 * ((y0 + y) * width + x0), 4 * y * size
        out.pixels[d : d + 4 * size] = small.pixels[s : s + 4 * size]
    return out


# The set


def images(source: Path) -> dict[str, bytes]:
    icon = trim(read_png(source))
    cache: dict[int, Image] = {}
    files: dict[str, bytes] = {}
    for scale in (1, 2):
        def s(v: float) -> int:
            return round(v * scale)

        suffix = f"scale-{scale * 100}"
        files[f"Square44x44Logo.{suffix}.png"] = png_bytes(place(icon, s(44), s(44), s(44), cache))
        files[f"Square150x150Logo.{suffix}.png"] = png_bytes(place(icon, s(150), s(150), s(84), cache))
        files[f"Wide310x150Logo.{suffix}.png"] = png_bytes(place(icon, s(310), s(150), s(84), cache))
        files[f"StoreLogo.{suffix}.png"] = png_bytes(place(icon, s(50), s(50), s(50), cache))
        files[f"SplashScreen.{suffix}.png"] = png_bytes(place(icon, s(620), s(300), s(160), cache))
    for size in TARGET_SIZES:
        png = png_bytes(place(icon, size, size, size, cache))
        files[f"Square44x44Logo.targetsize-{size}.png"] = png
        files[f"Square44x44Logo.targetsize-{size}_altform-unplated.png"] = png
    files["AppIcon.ico"] = ico(icon, cache)
    return files


def ico(icon: Image, cache: dict[int, Image]) -> bytes:
    return ico_bytes([(size, png_bytes(scaled(icon, size, cache))) for size in ICO_SIZES])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when a committed image differs")
    check = parser.parse_args(argv).check
    config = companion.load()
    source = config.path("windows.assets.source")
    out = config.path("windows.assets.out")
    if source is None or out is None:
        print("companion.json has no windows.assets (source, out)", file=sys.stderr)
        return 1
    files = images(source)
    if (dev := config.path("windows.assets.devSource")) is not None:
        files["Dev/AppIcon.ico"] = ico(trim(read_png(dev)), {})
    stale = [companion.rel(path, out) for path in companion.write_outputs(files, check, out)]
    if check and stale:
        print(f"{len(stale)} Windows images differ from the icon (run laching/tools/windows/assets.py): {', '.join(stale)}", file=sys.stderr)
        return 1
    print(f"ok: {len(files)} Windows images match the icon" if check else
          f"wrote {len(stale)} of {len(files)} images to {companion.rel(out, config.root)}")
    return 0


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
