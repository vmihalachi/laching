#!/usr/bin/env python3
"""Builds Partner Center "Import listings" folders from the store listing (`store.dir`/<lang>.md) and its images.

Usage (from the app's root): python3 laching/tools/store/import.py [--out DIR] [--max-mb N] [--max-langs N] [lang ...]
Partner Center fails on a big import (mybackhurts' 17 languages, 53 MB in one folder), so the listing always goes in
parts: DIR/part1, part2, … of at most --max-mb (20) and --max-langs (6) languages each, the source language in part1.
Then Partner Center → the submission → Store listings → Import listings → Import folder, once per part, in order:
each part fills its own languages (text, screenshots, captions) and the three art slots, and leaves the other languages
alone. After part1, check that a language outside it kept its listing. companion.json's `store.microsoft` names the
folders (`screenshots`/<lang>/*.png, `art`/), the screenshots (`shots`: one image and one caption per language each)
and the publisher.
Render each language's screenshots first: a language without its own is an error, since English images next to a
translated description look odd.

Each part's CSV keeps only the rows we fill and its own languages' columns (Partner Center leaves rows and languages
that aren't in the file alone), with the Field and ID values of Partner Center's own export. Text rows are written for
every slot, so a feature or caption we dropped is cleared. Image rows can't clear: remove a stale image by hand.
"""

import argparse
import csv
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from listing import load  # noqa: E402

ART = {  # Partner Center field -> file in art/ (README → Store display art)
    "StoreLogo720x1080": "poster-art.png",
    "StoreLogo1080x1080": "box-art.png",
    "PromoImage1920x1080": "super-hero-art.png",
}
ART_ID = {"StoreLogo720x1080": 600, "StoreLogo1080x1080": 601, "PromoImage1920x1080": 606}
TEXT = "Text"
IMAGE = "Relative path (or URL to file in Partner Center)"


def lines(fields, key):
    value = fields.get(key, "")
    return value.split("\n") if value else []


ROWS = [
    ("Description", 2, TEXT),
    ("ReleaseNotes", 3, TEXT),
    ("Title", 4, TEXT),
    ("ShortDescription", 8, TEXT),
    ("DevStudio", 9, TEXT),
    ("CopyrightTrademarkInformation", 12, TEXT),
    *[(f"DesktopScreenshot{i + 1}", 100 + i, IMAGE) for i in range(30)],
    *[(f"DesktopScreenshotCaption{i + 1}", 150 + i, TEXT) for i in range(30)],
    *[(field, ART_ID[field], IMAGE) for field in ART],
    *[(f"Feature{i + 1}", 700 + i, TEXT) for i in range(20)],
    *[(f"SearchTerm{i + 1}", 900 + i, TEXT) for i in range(7)],
]


def split(langs, sizes, fixed, max_bytes, max_langs):
    """Packs the languages, in order, into parts of at most max_langs and max_bytes (fixed counts once per part)."""
    parts, part, used = [], [], fixed
    for lang in langs:
        if part and (len(part) == max_langs or used + sizes[lang] > max_bytes):
            parts.append(part)
            part, used = [], fixed
        part.append(lang)
        used += sizes[lang]
    return parts + [part] if part else parts


def main():
    listing = load()
    microsoft = listing.microsoft
    if microsoft is None:
        sys.exit("companion.json has no store.microsoft")
    folder = listing.dir
    screenshots_dir = folder / microsoft.get("screenshots", "microsoft/screenshots")
    art_dir = folder / microsoft.get("art", "microsoft/art")
    count = len(microsoft["shots"])
    name = listing.config.require("name")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(listing.config.root / f"build/store-import/microsoft/{name}-listings"))
    parser.add_argument("--max-mb", type=float, default=20, help="the most one part may weigh (default 20)")
    parser.add_argument("--max-langs", type=int, default=6, help="the most languages in one part (default 6)")
    parser.add_argument("langs", nargs="*", default=listing.langs)
    args = parser.parse_args()
    # Partner Center wants the package's language codes, lowercased (Strings/<lang> in the app).
    column = {lang: lang.lower() for lang in listing.langs}
    langs = sorted(args.langs, key=lambda lang: lang != listing.source)  # the source language in part1

    shots, captions, problems = {}, {}, []
    for lang in langs:
        shots[lang] = sorted((screenshots_dir / lang).glob("*.png"))
        if len(shots[lang]) != count:
            problems.append(f"{lang}: {len(shots[lang])} screenshots in {screenshots_dir / lang}, want {count}")
            continue
        captions[lang] = lines(listing.fields(lang), "ms.captions")
        if len(captions[lang]) != count:
            problems.append(f"{lang}: {len(captions[lang])} captions, want {count}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        sys.exit(1)

    sizes = {lang: sum(shot.stat().st_size for shot in shots[lang]) for lang in langs}
    art_size = sum((art_dir / file).stat().st_size for file in ART.values())
    parts = split(langs, sizes, art_size, args.max_mb * 1e6, args.max_langs)
    for lang in langs:
        if art_size + sizes[lang] > args.max_mb * 1e6:
            print(f"{lang}: {(art_size + sizes[lang]) / 1e6:.1f} MB with the art, over --max-mb {args.max_mb:g}",
                  file=sys.stderr)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    for n, part_langs in enumerate(parts, 1):
        part = out / f"part{n}"
        base = part.name  # Partner Center paths start at the folder you pick
        (part / "art").mkdir(parents=True)
        for file in ART.values():
            shutil.copy2(art_dir / file, part / "art" / file)
        columns = {}
        for lang in part_langs:
            fields = listing.fields(lang)
            (part / lang).mkdir()
            for shot in shots[lang]:
                shutil.copy2(shot, part / lang / shot.name)
            col = {
                "Description": fields["ms.description"],
                "ReleaseNotes": "",
                "Title": fields["ms.name"],
                "ShortDescription": fields["ms.short"],
                "DevStudio": microsoft.get("publisher", ""),
                "CopyrightTrademarkInformation": fields["ms.copyright"],
            }
            for i in range(30):
                col[f"DesktopScreenshot{i + 1}"] = f"{base}/{lang}/{shots[lang][i].name}" if i < count else ""
                col[f"DesktopScreenshotCaption{i + 1}"] = captions[lang][i] if i < count else ""
            for field, file in ART.items():
                col[field] = f"{base}/art/{file}"
            features, search = lines(fields, "ms.features"), lines(fields, "ms.search")
            for i in range(20):
                col[f"Feature{i + 1}"] = features[i] if i < len(features) else ""
            for i in range(7):
                col[f"SearchTerm{i + 1}"] = search[i] if i < len(search) else ""
            columns[column[lang]] = col
        codes = list(columns)
        # UTF-8 with a BOM and CRLF, like Partner Center's export.
        with open(part / "listings.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, lineterminator="\r\n")
            writer.writerow(["Field", "ID", "Type (Type)", "default", *codes])
            for field, id_, kind in ROWS:
                writer.writerow([field, id_, kind, "", *[columns[c][field] for c in codes]])
        size = art_size + sum(sizes[lang] for lang in part_langs)
        print(f"{part}  ({size / 1e6:.1f} MB: {' '.join(part_langs)})")
    print(f"Import the {len(parts)} part folders one after another (Import listings → Import folder).")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 on a cp1252 Windows console too (companion.utf8_console)
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
