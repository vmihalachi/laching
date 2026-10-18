#!/usr/bin/env python3
"""Builds a Partner Center "Import listings" folder from the store listing (`store.dir`/<lang>.md) and its images.

Usage (from the app's root): python3 laching/tools/store/import.py [--out DIR] [lang ...]
Then Partner Center → the submission → Store listings → Import listings → Import folder, and pick the folder this
prints. One import fills every language: text, screenshots, captions and the three art slots. companion.json's
`store.microsoft` names the folders (`screenshots`/<lang>/*.png, `art`/), the screenshots (`shots`: one image and one
caption per language each) and the publisher.
Render each language's screenshots first: a language without its own is an error, since English images next to a
translated description look odd.

The CSV keeps only the rows we fill (Partner Center leaves rows that aren't in the file alone), with the Field and ID
values of Partner Center's own export. Text rows are written for every slot, so a feature or caption we dropped is
cleared. Image rows can't clear: remove a stale image by hand.
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
    parser.add_argument("langs", nargs="*", default=listing.langs)
    args = parser.parse_args()
    # Partner Center wants the package's language codes, lowercased (Strings/<lang> in the app).
    column = {lang: lang.lower() for lang in listing.langs}

    out = Path(args.out)
    base = out.name  # Partner Center paths start at the folder you pick
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    columns = {}
    problems = []
    for lang in args.langs:
        fields = listing.fields(lang)
        shots = sorted((screenshots_dir / lang).glob("*.png"))
        if len(shots) != count:
            problems.append(f"{lang}: {len(shots)} screenshots in {screenshots_dir / lang}, want {count}")
            continue
        (out / lang).mkdir()
        for shot in shots:
            shutil.copy2(shot, out / lang / shot.name)
        captions = lines(fields, "ms.captions")
        if len(captions) != count:
            problems.append(f"{lang}: {len(captions)} captions, want {count}")
        col = {
            "Description": fields["ms.description"],
            "ReleaseNotes": "",
            "Title": fields["ms.name"],
            "ShortDescription": fields["ms.short"],
            "DevStudio": microsoft.get("publisher", ""),
            "CopyrightTrademarkInformation": fields["ms.copyright"],
        }
        for i in range(30):
            col[f"DesktopScreenshot{i + 1}"] = f"{base}/{lang}/{shots[i].name}" if i < len(shots) else ""
            col[f"DesktopScreenshotCaption{i + 1}"] = captions[i] if i < len(captions) else ""
        for field, file in ART.items():
            col[field] = f"{base}/art/{file}"
        features, search = lines(fields, "ms.features"), lines(fields, "ms.search")
        for i in range(20):
            col[f"Feature{i + 1}"] = features[i] if i < len(features) else ""
        for i in range(7):
            col[f"SearchTerm{i + 1}"] = search[i] if i < len(search) else ""
        columns[column[lang]] = col

    if problems:
        print("\n".join(problems), file=sys.stderr)
        sys.exit(1)

    (out / "art").mkdir()
    for file in ART.values():
        shutil.copy2(art_dir / file, out / "art" / file)

    rows = [
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
    codes = list(columns)
    # UTF-8 with a BOM and CRLF, like Partner Center's export.
    with open(out / "listings.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n")
        writer.writerow(["Field", "ID", "Type (Type)", "default", *codes])
        for field, id_, kind in rows:
            writer.writerow([field, id_, kind, "", *[columns[c][field] for c in codes]])
    print(out)


if __name__ == "__main__":
    main()
