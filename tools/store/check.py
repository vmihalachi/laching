#!/usr/bin/env python3
"""Checks the store listings (`store.dir`/<lang>.md) against each store's field limits.

Usage (from the app's root): python3 laching/tools/store/check.py [lang ...]
Exits 1 on any problem. Each field is a `### Heading `field.id`` followed by a ```text block. The fields are the App
Store's, each in-app purchase's (`store.apple.iaps`), the iPhone and iPad version page's when the app has
`store.apple.ios`, the Microsoft Store's when it has `store.microsoft`, and Google Play's (`google.*`: title 30, short
description 80, full description 4000, each product's name 55 and description 200) when it has `store.google`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from listing import KEYWORDS, MAX_LINES, OPTIONAL, Listing, load, parse  # noqa: E402


def words(text):
    return {w for w in re.split(r"[^\w-]+", text.lower()) if w}


def check(listing: Listing, fields: dict[str, str], reference: dict[str, str]) -> list[str]:
    limits, single_line = listing.limits, listing.single_line
    problems = []
    for key in limits:
        if key not in fields and key not in OPTIONAL:
            problems.append(f"missing field {key}")
    for key, value in fields.items():
        limit = limits.get(key)
        if limit is None:
            problems.append(f"{key}: unknown field")
            continue
        if not value.strip():
            problems.append(f"{key}: empty")
        if key in MAX_LINES:
            lines = value.split("\n")
            if len(lines) > MAX_LINES[key]:
                problems.append(f"{key}: {len(lines)} lines, max {MAX_LINES[key]}")
            for line in lines:
                if len(line) > limit:
                    problems.append(f"{key}: {len(line)} > {limit} characters: {line}")
        elif key in KEYWORDS:
            size = len(value.encode("utf-8"))
            if size > limit:
                problems.append(f"{key}: {size} > {limit} bytes")
            if " ," in value or ", " in value:
                problems.append(f"{key}: no spaces around commas (they count toward the limit)")
            terms = value.split(",")
            if len(terms) != len(set(t.lower() for t in terms)):
                problems.append(f"{key}: repeated keyword")
            # Words already in the name or subtitle are indexed there; repeating them wastes bytes.
            taken = words(fields.get("apple.name", "")) | words(fields.get("apple.subtitle", ""))
            for term in terms:
                if term.lower() in taken:
                    problems.append(f"{key}: '{term}' is already in the name or subtitle")
        elif len(value) > limit:
            problems.append(f"{key}: {len(value)} > {limit} characters")
        if key in single_line and "\n" in value:
            problems.append(f"{key}: must be one line")
    search = fields.get("ms.search", "")
    if len(search.split()) > 21:
        problems.append(f"ms.search: {len(search.split())} words, max 21 across all terms")
    if "ms.captions" in fields and "ms.captions" in reference:
        have, want = len(fields["ms.captions"].split("\n")), len(reference["ms.captions"].split("\n"))
        if have != want:
            problems.append(f"ms.captions: {have} captions, {listing.source} has {want} (one per screenshot)")
    return problems


def sizes(fields: dict[str, str], prefix: str, label: str) -> str:
    keywords, promo = fields.get(f"{prefix}.keywords", ""), fields.get(f"{prefix}.promo", "")
    return f"{label}keywords {len(keywords.encode('utf-8'))}/100 bytes, {label}promo {len(promo)}/170"


def run(listing: Listing, langs: list[str]) -> bool:
    """Prints a line per language (and its problems); True when every one is fine."""
    reference = parse(listing.dir / f"{listing.source}.md")
    ok = True
    for lang in langs:
        path = listing.dir / f"{lang}.md"
        if not path.exists():
            print(f"{lang}: no {path.name}")
            ok = False
            continue
        fields = parse(path)
        problems = check(listing, fields, reference)
        summary = sizes(fields, "apple", "")
        if listing.ios is not None:
            summary += ", " + sizes(fields, "ios", "iOS ")
        if listing.google is not None:
            summary += (f", Play title {len(fields.get('google.title', ''))}/30"
                        f", short {len(fields.get('google.short', ''))}/80")
        if problems:
            ok = False
            print(f"{lang}: {len(problems)} problem(s) ({summary})")
            for problem in problems:
                print(f"  {problem}")
        else:
            print(f"{lang}: ok ({summary})")
    return ok


def main(argv: list[str]) -> int:
    listing = load()
    return 0 if run(listing, argv or listing.langs) else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 on a cp1252 Windows console too (companion.utf8_console)
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
