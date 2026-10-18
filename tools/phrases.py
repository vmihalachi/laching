#!/usr/bin/env python3
"""Keeps the String Catalog's `phrase.<id>` keys in step with the app's phrase bank.

The bank (in the app's Swift core) is the only place a line is written; its source text reaches the catalog through
the generated `<conformance.dir>/phrases.json`, so regenerate the vectors first (`laching/tools/conformance.sh`). The
app looks these keys up by name and Xcode never extracts them, so they're `manual` catalog entries added the way
`l10n.py add` adds them.

    python3 laching/tools/phrases.py sync     # add missing keys, update changed text, drop retired lines
    python3 laching/tools/phrases.py check    # exit 1 when a key is missing, stale or orphaned, or a weight is under 1

A line is one key, `phrase.<id>`; a joke told in beats is one key per part, `phrase.<id>.1`, `.2`, …, each with a
comment saying which part it is. `sync` owns these entries: when a line's source text changed in the bank it updates
it and drops that key's translations (a rewrite is re-transcreated from scratch, not patched), and it removes the
entries of retired lines and parts. It keeps every key's translator comment in step with the phrase: the
`l10n.phraseComment` template (`{character}`, `{tags}`, `{flavor}`, `{flavorNote}`, `{part}`), and for a phrase that
exists in some languages only (`languages` in phrases.json), a note naming them; `l10n.py` skips its keys elsewhere.

`check` also reports, by id, a phrase whose weight is under 1: the picker's weighted draw needs a positive total, and
the app's `Phrase` traps on such a weight before the character says a word.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion  # noqa: E402
import l10n  # noqa: E402

PREFIX = "phrase."
DEFAULT_COMMENT = "{character} says this aloud ({tags}). Flavor {flavorNote}.{part}"


def read_bank(config: companion.Config) -> list[dict]:
    """The phrases of the generated phrases.json, tunes included."""
    path = config.require_phrases_file()
    if not path.exists():
        sys.exit(f"{path.relative_to(config.root)} is missing: regenerate the vectors first (laching/tools/conformance.sh)")
    return json.loads(path.read_text("utf8"))["phrases"]


def underweight(bank: list[dict]) -> list[dict]:
    """The phrases whose weight is under 1, in bank order."""
    return [phrase for phrase in bank if phrase["weight"] < 1]


def load_bank(config: companion.Config) -> dict[str, dict]:
    """Catalog key → {"source": the key's text, "phrase": its phrase, "part": (n, count) or None}."""
    out: dict[str, dict] = {}
    for phrase in read_bank(config):
        if phrase["kind"] == "line":
            out[phrase["key"]] = {"source": phrase["english"], "phrase": phrase, "part": None}
        elif phrase["kind"] == "sequence":
            count = len(phrase["parts"])
            for n, (key, text) in enumerate(zip(phrase["keys"], phrase["parts"]), start=1):
                out[key] = {"source": text, "phrase": phrase, "part": (n, count)}
    return out


def catalog_source(config: companion.Config) -> dict[str, str]:
    data = json.loads(config.require_path("l10n.catalog").read_text("utf8"))
    source = data.get("sourceLanguage", config.source_language)
    out = {}
    for key, entry in data["strings"].items():
        if not key.startswith(PREFIX):
            continue
        unit = entry.get("localizations", {}).get(source, {}).get("stringUnit")
        out[key] = unit["value"] if unit else key
    return out


def comment_for(config: companion.Config, item: dict) -> str:
    phrase = item["phrase"]
    flavor = phrase.get("flavor") or ""
    notes = config.get("l10n.flavorNotes", {})
    part = f" Part {item['part'][0]} of {item['part'][1]} of a joke told in beats." if item["part"] else ""
    comment = (config.get("l10n.phraseComment") or DEFAULT_COMMENT).format(
        character=config.get("character", ""), tags=", ".join(phrase["tags"]), flavor=flavor,
        flavorNote=notes.get(flavor, flavor), part=part,
    )
    languages = phrase.get("languages")
    if languages:
        comment += f" Exists only in {', '.join(languages)}: don't translate it into other languages."
    return comment


def diff(config: companion.Config) -> tuple[dict[str, dict], list[str], list[str]]:
    bank = load_bank(config)
    catalog = catalog_source(config)
    missing = {k: item for k, item in bank.items() if k not in catalog}
    stale = [k for k, item in bank.items() if k in catalog and catalog[k] != item["source"]]
    orphans = [k for k in catalog if k not in bank]
    return missing, stale, orphans


def cmd_sync(config: companion.Config) -> int:
    missing, stale, orphans = diff(config)
    if missing:
        payload = {k: {"comment": comment_for(config, item), "en": item["source"]} for k, item in missing.items()}
        l10n.configure(config)
        if result := l10n.print_added(*l10n.add_strings(payload)):
            return result
    else:
        print("added 0")
    bank = load_bank(config)
    catalog_path = config.require_path("l10n.catalog")
    data = l10n.load_catalog(catalog_path)
    strings = data["strings"]
    source = data.get("sourceLanguage", config.source_language)
    for key in stale:
        entry = strings[key]
        entry["localizations"] = {}
        l10n.set_catalog_value(entry, source, bank[key]["source"])
        print(f"  ~ {key!r}: source text updated, translations dropped (re-export them)")
    for key in orphans:
        del strings[key]
        print(f"  - {key!r}: retired, removed")
    comments = 0
    for key, item in bank.items():
        if key in strings and strings[key].get("comment") != comment_for(config, item):
            strings[key]["comment"] = comment_for(config, item)
            comments += 1
    if stale or orphans or comments:
        l10n.dump_catalog(catalog_path, data)
    print(f"updated {len(stale)}, removed {len(orphans)}, comments refreshed {comments}")
    return 0


def cmd_check(config: companion.Config) -> int:
    light = underweight(read_bank(config))
    for phrase in light:
        print(f"weight under 1 in the bank: {phrase['id']!r} has weight {phrase['weight']} (the picker needs 1 or more)")
    missing, stale, orphans = diff(config)
    for key in missing:
        print(f"missing in the catalog: {key!r} (run phrases.py sync)")
    for key in stale:
        print(f"source text changed in the bank: {key!r} (run phrases.py sync, then re-export the translations)")
    for key in orphans:
        print(f"in the catalog but not in the bank: {key!r} (retired? phrases.py sync removes it)")
    return 1 if light or missing or stale or orphans else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("command", choices=("sync", "check"))
    command = parser.parse_args(argv).command
    config = companion.load()
    return cmd_sync(config) if command == "sync" else cmd_check(config)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
