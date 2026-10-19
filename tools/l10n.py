#!/usr/bin/env python3
"""Localization helper for a companion app's String Catalogs and its landing page's locale files.

Lets you (and coding agents) find and fill translation gaps without opening a large `Localizable.xcstrings`. Every
name and path comes from the app's `companion.json` (`languages`, `l10n`, `mac`, `windows`, `conformance`). Python 3
standard library only. Run from anywhere inside the app:

  python3 laching/tools/l10n.py sync                 # build, then xcstringstool sync the catalog
  python3 laching/tools/l10n.py status               # summary of gaps per language
  python3 laching/tools/l10n.py status -v            # ...with one line per problem
  python3 laching/tools/l10n.py export [--lang it] [--since REF]
                                                      # write work files to <mac.dir>/build/l10n/<lang>.json
  python3 laching/tools/l10n.py apply <mac.dir>/build/l10n/*.json
  python3 laching/tools/l10n.py prune                # delete keys Xcode marked stale
  python3 laching/tools/l10n.py add FILE.json        # add strings only the Windows/Linux ports use
  python3 laching/tools/l10n.py check                # exit 1 if anything is missing or invalid,
                                                      # or the Windows/Linux exports are stale
  python3 laching/tools/l10n.py export-windows [--out DIR] [--dry-run]
                                                      # .resw per language for the Windows port
  python3 laching/tools/l10n.py export-linux [--out DIR] [--dry-run]
                                                      # .po per language for the Linux port

**Language-scoped phrases.** A phrase in `<conformance.dir>/phrases.json` with `languages` (say `["it"]`) exists in
those languages only: its keys (`phrase.<id>`, or `.1`, `.2` for a joke's parts) are required there, and in every other
language they are out of scope, so `status`, `check` and `export` skip them and `export-windows` / `export-linux` leave
them out (the source language always keeps them).

A work file lists only the strings that need a translation in one language:

  {
    "lang": "it",
    "rules": "...",
    "glossary": {"Say something": "Dì qualcosa", ...},
    "app": {"<key>": {"en": "...", "comment": "...", "current": "...", "value": ""}},
    "plist": {...},
    "landing": {"<dotted.key>": {"en": "...", "current": "...", "value": ""}}
  }

Plural strings have `"en": {"one": ..., "other": ...}` and need `"value"` to be an object with every
category listed in `"categories"`. Fill in `"value"` and leave everything else alone. `apply` checks that
format specifiers (%@, %lld, %1$@ …), `{placeholders}` and HTML tags match the English text, and it
writes the files in the same format Xcode and the repo use, so diffs stay small.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion  # noqa: E402
from companion import base_language, fail, git  # noqa: E402

# Set by `configure` from the app's companion.json (tests point them elsewhere).
CONFIG: companion.Config
ROOT: Path
MACOS: Path
CATALOGS: dict[str, Path] = {}
LANDING_I18N: Path | None = None
WORK_DIR: Path
DERIVED: Path
SOURCE = "en"
LANGUAGES: list[str] = []
WINDOWS_STRINGS: Path | None = None
LINUX_PO: Path | None = None
ANDROID_STRINGS: Path | None = None  # the app module's res/ folder: values*/strings.xml and xml/locales_config.xml
ANDROID_DIR: Path | None = None
GLOSSARY_KEYS: list[str] = []
# (source file, pattern with one group): more glossary terms, read from the app's code (`l10n.glossarySources`).
GLOSSARY_SOURCES: list[tuple[Path, re.Pattern]] = []
RULES = ""
# Plural categories each language must provide, matching the catalog's existing convention.
PLURAL_CATEGORIES: dict[str, list[str]] = {}
DEFAULT_CATEGORIES = ["one", "other"]
# Catalog key → the base language codes its phrase exists in (only for language-scoped phrases).
SCOPED: dict[str, set[str]] = {}

BASE_RULES = (
    "Transcreate `en` into the target language and put the result in `value`. Never translate word for word: "
    "write what a native copywriter would put in that spot (a button, a note, a heading, a line of page copy), "
    "with the language's own idioms, word order and register, even when the words end up different from the "
    "English. A calque that reads as translated is a bug, even if it is accurate. Where you can, avoid wording "
    "that assumes the reader's gender. UI text is friendly and brief, as a native app in that language would say "
    "it. Reuse the terms in `current`, `glossary` and the existing translations. Keep every format specifier "
    "(%@, %lld, %1$@ …), {placeholder} and HTML tag exactly as in `en`; you may reorder positional ones (%1$@, "
    "%2$@). For plurals, `value` is an object with every key in `categories`, each including the number. If an "
    "item has `en_plural`, decide whether the count needs singular/plural forms: if it does, fill `en_plural.one` "
    "(English singular, same specifiers) and give `value` as a plural object; otherwise leave `en_plural` alone and "
    "give a string. If an item has `substitutions`, its `en` holds a `%#@name@` token that stands for a counted "
    "phrase: keep the token in `value`, and for each name fill `substitutions.<name>.value` with every key in "
    "`categories`, each containing `%arg` where the number goes (see `substitutions.<name>.en`)."
)


def configure(config: companion.Config | None = None) -> None:
    """Points the module at an app: its catalogs, languages, rules and export folders."""
    global CONFIG, ROOT, MACOS, LANDING_I18N, WORK_DIR, DERIVED, SOURCE, LANGUAGES, WINDOWS_STRINGS, LINUX_PO, ANDROID_STRINGS
    global ANDROID_DIR
    global GLOSSARY_KEYS, GLOSSARY_SOURCES, RULES, PLURAL_CATEGORIES, SCOPED
    CONFIG = config or companion.load()
    ROOT = CONFIG.root
    MACOS = CONFIG.mac_dir
    CATALOGS.clear()
    CATALOGS["app"] = CONFIG.require_path("l10n.catalog")
    if CONFIG.get("l10n.plist"):
        CATALOGS["plist"] = CONFIG.require_path("l10n.plist")
    LANDING_I18N = CONFIG.path("l10n.landing")
    WORK_DIR = CONFIG.work_dir / "l10n"
    DERIVED = CONFIG.derived_data
    SOURCE = CONFIG.source_language
    LANGUAGES = CONFIG.languages
    WINDOWS_STRINGS = CONFIG.path("windows.strings")
    LINUX_PO = CONFIG.path("l10n.linuxPo")
    # `android.strings`, or `l10n.androidStrings`, the name it had before the Android section.
    ANDROID_STRINGS = CONFIG.path("android.strings") or CONFIG.path("l10n.androidStrings")
    ANDROID_DIR = CONFIG.path("android.dir")
    GLOSSARY_KEYS = list(CONFIG.get("l10n.glossaryKeys", []))
    GLOSSARY_SOURCES = [
        (ROOT / source["file"], re.compile(source["pattern"])) for source in CONFIG.get("l10n.glossarySources", [])
    ]
    PLURAL_CATEGORIES = dict(CONFIG.get("l10n.pluralCategories", {}))
    rules_file = CONFIG.path("l10n.rulesFile")
    app_rules = rules_file.read_text("utf8").strip() if rules_file and rules_file.exists() else ""
    names = CONFIG.get("l10n.keepNames", [])
    keep = f" Keep the names {', '.join(names)} as they are." if names else ""
    RULES = " ".join(part for part in (BASE_RULES, app_rules) if part) + keep
    SCOPED = scoped_keys(CONFIG.phrases_file)


def scoped_keys(phrases_file: Path | None) -> dict[str, set[str]]:
    """The catalog keys of phrases that exist in some languages only, with those languages (none without a bank)."""
    if phrases_file is None or not phrases_file.exists():
        return {}
    out: dict[str, set[str]] = {}
    for phrase in json.loads(phrases_file.read_text("utf8"))["phrases"]:
        languages = phrase.get("languages")
        if not languages:
            continue
        for key in phrase.get("keys") or ([phrase["key"]] if phrase.get("key") else []):
            out[key] = set(languages)
    return out


def in_scope(key: str, lang: str) -> bool:
    """Whether `key` needs a translation in `lang`: always, unless it's a phrase that doesn't exist there."""
    languages = SCOPED.get(key)
    return languages is None or lang == SOURCE or base_language(lang) in languages


SPEC_RE = re.compile(r"%(?:(\d+)\$)?[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|q|z|t|j)?([@dDiuUxXoOfFeEgGcCsSpaA%])")
BRACE_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
INT_SPEC_RE = re.compile(r"%(?:\d+\$)?(?:ll|l|q|z)?[diu]")


# ---------------------------------------------------------------------------------------------- I/O


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text("utf8"))


def dump_catalog(path: Path, data: dict) -> None:
    # Xcode's format: 2-space indent, " : " separators, no ASCII escaping. Keep the file's trailing
    # newline (or lack of one) as it is.
    newline = "\n" if path.exists() and path.read_text("utf8").endswith("\n") else ""
    text = json.dumps(data, indent=2, ensure_ascii=False, separators=(",", " : ")) + newline
    path.write_text(text, "utf8")


def load_landing(code: str) -> dict:
    assert LANDING_I18N is not None
    return json.loads((LANDING_I18N / f"{code}.json").read_text("utf8"))


def dump_landing(code: str, data: dict) -> None:
    assert LANDING_I18N is not None
    (LANDING_I18N / f"{code}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf8")


def app_languages() -> list[str]:
    """The languages the app ships besides the source (`languages.all`)."""
    return [code for code in LANGUAGES if code != SOURCE]


def landing_languages() -> list[str]:
    if LANDING_I18N is None or not (LANDING_I18N / "languages.ts").exists():
        return []
    text = (LANDING_I18N / "languages.ts").read_text("utf8")
    return [code for code in re.findall(r'code:\s*"([^"]+)"', text) if code != SOURCE]


def flatten(tree: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def set_path(tree: dict, dotted: str, value: object, reference: dict) -> None:
    """Sets `tree[a][b][c]`, creating missing levels in the same position as in `reference`."""
    parts = dotted.split(".")
    node, ref = tree, reference
    for part in parts[:-1]:
        ref = ref.get(part, {}) if isinstance(ref, dict) else {}
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    # Keep keys in the English file's order so the diff only shows the new line.
    if isinstance(ref, dict) and ref:
        order = [key for key in ref if key in node] + [key for key in node if key not in ref]
        items = [(key, node[key]) for key in order]
        node.clear()
        node.update(items)


def git_repository(folder: Path) -> Path | None:
    """The top of the git repository `folder` lies in (a submodule's or nested checkout's own), or None."""
    while not folder.exists():
        folder = folder.parent
    result = git(folder, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def git_show(ref: str, path: Path) -> str | None:
    """`path` as it was at `ref`, or None. A path inside a submodule or any nested repository (a landing page kept in
    its own repository) is read from that repository: at the commit the app's `ref` records for it, else (not recorded
    there, or that commit isn't fetched) at that repository's own `ref`."""
    path = path.resolve()
    repo = git_repository(path.parent)
    if repo is None:
        return None
    app = git_repository(ROOT.resolve())
    if app is not None and repo != app and repo.is_relative_to(app):
        recorded = git(app, "rev-parse", "--verify", "--quiet", f"{ref}:{repo.relative_to(app).as_posix()}")
        commit = recorded.stdout.strip()
        if recorded.returncode == 0 and git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0:
            ref = commit
    result = git(repo, "show", f"{ref}:{path.relative_to(repo).as_posix()}")
    return result.stdout if result.returncode == 0 else None


# ------------------------------------------------------------------------------------- validation


def specifiers(text: str) -> list[str]:
    # Positional indices (%1$@) are dropped so translations may reorder arguments.
    return sorted(re.sub(r"^%\d+\$", "%", m.group(0)) for m in SPEC_RE.finditer(text) if m.group(2) != "%")


def markup(text: str) -> list[str]:
    return sorted(BRACE_RE.findall(text) + TAG_RE.findall(text))


def token_problem(english: str, translated: str) -> str | None:
    if specifiers(english) != specifiers(translated):
        return f"format specifiers {specifiers(english)} != {specifiers(translated)}"
    if markup(english) != markup(translated):
        return f"placeholders/tags {markup(english)} != {markup(translated)}"
    return None


def categories_for(lang: str) -> list[str]:
    return PLURAL_CATEGORIES.get(lang, DEFAULT_CATEGORIES)


# ------------------------------------------------------------------------------------ app catalogs


def english_of(key: str, entry: dict):
    """English text for a catalog entry: a string, or {category: text} for plurals."""
    loc = entry.get("localizations", {}).get(SOURCE, {})
    if "variations" in loc:
        return {cat: unit["stringUnit"]["value"] for cat, unit in loc["variations"].get("plural", {}).items()}
    if "stringUnit" in loc:
        return loc["stringUnit"]["value"]
    return key


def translation_of(entry: dict, lang: str):
    loc = entry.get("localizations", {}).get(lang)
    if not loc:
        return None, None
    if "variations" in loc:
        plural = loc["variations"].get("plural", {})
        states = {unit.get("stringUnit", {}).get("state") for unit in plural.values()}
        state = "translated" if states == {"translated"} else next(iter(states - {"translated"}), None)
        return {cat: unit.get("stringUnit", {}).get("value", "") for cat, unit in plural.items()}, state
    unit = loc.get("stringUnit", {})
    return unit.get("value"), unit.get("state")


def english_substitutions(entry: dict) -> dict:
    """The source language's `%#@name@` substitutions of a catalog entry: {name: {argNum, formatSpecifier, plural}}."""
    subs = entry.get("localizations", {}).get(SOURCE, {}).get("substitutions", {})
    return {
        name: {
            "argNum": sub["argNum"],
            "formatSpecifier": sub["formatSpecifier"],
            "plural": {cat: unit["stringUnit"]["value"] for cat, unit in sub["variations"]["plural"].items()},
        }
        for name, sub in subs.items()
    }


def substitution_issues(entry: dict, lang: str) -> list[str]:
    """Each source substitution must have every plural category `lang` uses, filled in."""
    english = english_substitutions(entry)
    if not english:
        return []
    subs = entry.get("localizations", {}).get(lang, {}).get("substitutions", {})
    issues = []
    for name in english:
        plural = subs.get(name, {}).get("variations", {}).get("plural", {})
        missing = [cat for cat in categories_for(lang) if not plural.get(cat, {}).get("stringUnit", {}).get("value")]
        if missing:
            issues.append(f"substitution {name}: missing {', '.join(missing)}")
    return issues


def catalog_issues(entry_key: str, entry: dict, lang: str) -> list[str]:
    if entry.get("shouldTranslate") is False or entry.get("extractionState") == "stale":
        return []
    if not in_scope(entry_key, lang):
        return []
    english = english_of(entry_key, entry)
    value, state = translation_of(entry, lang)
    if value is None:
        return ["missing"]
    issues = []
    if state != "translated":
        issues.append(f"state {state}")
    if isinstance(english, dict):
        if not isinstance(value, dict):
            return issues + ["needs plural variations"]
        missing = [cat for cat in categories_for(lang) if not value.get(cat)]
        if missing:
            issues.append(f"missing plural {', '.join(missing)}")
        reference = english.get("other", next(iter(english.values())))
        for cat, text in value.items():
            if text and (problem := token_problem(reference, text)):
                issues.append(f"{cat}: {problem}")
    else:
        if isinstance(value, dict):
            reference = value.get("other", "")
            if problem := token_problem(english, reference):
                issues.append(problem)
        elif not value:
            issues.append("empty")
        elif problem := token_problem(english, value):
            issues.append(problem)
        else:
            issues += substitution_issues(entry, lang)
    return issues


def changed_english_keys(section: str, since: str | None) -> set[str]:
    if not since:
        return set()
    old_text = git_show(since, CATALOGS[section])
    if old_text is None:
        return set()
    old = json.loads(old_text)["strings"]
    new = load_catalog(CATALOGS[section])["strings"]
    return {
        key for key, entry in new.items()
        if key in old and english_of(key, entry) != english_of(key, old[key])
    }


# ---------------------------------------------------------------------------------------- landing


def landing_issues(lang: str) -> dict[str, list[str]]:
    english = flatten(load_landing(SOURCE))
    try:
        translated = flatten(load_landing(lang))
    except FileNotFoundError:
        return {key: ["missing file"] for key in english}
    issues: dict[str, list[str]] = {}
    for key, text in english.items():
        if key not in translated:
            issues[key] = ["missing"]
        elif not str(translated[key]).strip():
            issues[key] = ["empty"]
        elif isinstance(text, str) and (problem := token_problem(text, str(translated[key]))):
            issues[key] = [problem]
    for key in translated:
        if key not in english:
            issues[key] = ["extra (not in en.json)"]
    return issues


def changed_landing_keys(since: str | None) -> set[str]:
    if not since or not landing_languages():
        return set()
    old_text = git_show(since, LANDING_I18N / f"{SOURCE}.json")
    if old_text is None:
        return set()
    old = flatten(json.loads(old_text))
    new = flatten(load_landing(SOURCE))
    return {key for key, value in new.items() if key in old and old[key] != value}


# --------------------------------------------------------------------------------------- commands


def collect(since: str | None = None) -> dict[str, dict[str, dict[str, list[str]]]]:
    """{lang: {section: {key: [issues]}}} for every language the app or landing page ships."""
    langs = sorted(set(app_languages()) | set(landing_languages()))
    result = {lang: {"app": {}, "plist": {}, "landing": {}} for lang in langs}
    for section, path in CATALOGS.items():
        strings = load_catalog(path)["strings"]
        changed = changed_english_keys(section, since)
        for lang in app_languages():
            for key, entry in strings.items():
                issues = catalog_issues(key, entry, lang)
                if key in changed and not issues:
                    issues = [f"English changed since {since}"]
                if issues:
                    result[lang][section][key] = issues
    changed = changed_landing_keys(since)
    for lang in landing_languages():
        issues = landing_issues(lang)
        for key in changed:
            issues.setdefault(key, [f"English changed since {since}"])
        result[lang]["landing"] = issues
    return result


def stale_keys() -> dict[str, list[str]]:
    return {
        section: [key for key, entry in load_catalog(path)["strings"].items() if entry.get("extractionState") == "stale"]
        for section, path in CATALOGS.items()
    }


def cmd_status(args) -> int:
    return report(args, strict=False)


def cmd_check(args) -> int:
    result = report(args, strict=True)
    stale = stale_exports()
    for path in stale:
        print(f"stale export: {path.relative_to(ROOT).as_posix()} (run `export-windows` / `export-linux` / `export-android`)")
    return 1 if stale else result


def report(args, strict: bool) -> int:
    """The gaps per language (one line per problem with `args.verbose`) and the stale keys; with `strict`, 1 on any."""
    report = collect(args.since)
    total = 0
    for lang, sections in report.items():
        counts = {section: len(items) for section, items in sections.items()}
        total += sum(counts.values())
        summary = ", ".join(f"{section} {count}" for section, count in counts.items() if count) or "ok"
        print(f"{lang}: {summary}")
        if args.verbose:
            for section, items in sections.items():
                for key, issues in items.items():
                    print(f"  [{section}] {key!r}: {'; '.join(issues)}")
    for section, keys in stale_keys().items():
        if keys:
            total += len(keys)
            print(f"stale in {CATALOGS[section].name}: {len(keys)} (run `prune` if the code no longer uses them)")
            if args.verbose:
                for key in keys:
                    print(f"  {key!r}")
    return 1 if total and strict else 0


def glossary_keys() -> list[str]:
    """The fixed UI terms (GLOSSARY_KEYS), then the ones found in the app's code (GLOSSARY_SOURCES), each once."""
    keys = list(GLOSSARY_KEYS)
    for path, pattern in GLOSSARY_SOURCES:
        if not path.exists():
            fail(f"l10n.glossarySources: {path.relative_to(ROOT)} not found")
        keys += pattern.findall(path.read_text("utf8"))
    return list(dict.fromkeys(keys))


def glossary(strings: dict, lang: str) -> dict[str, str]:
    """The glossary terms (`glossary_keys`) and their current translations."""
    out = {}
    for name in glossary_keys():
        value, _ = translation_of(strings.get(name, {}), lang)
        if isinstance(value, str) and value:
            out[name] = value
    return out


def cmd_export(args) -> int:
    report = collect(args.since)
    langs = args.lang or list(report)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    catalogs = {section: load_catalog(path)["strings"] for section, path in CATALOGS.items()}
    landing_en = flatten(load_landing(SOURCE)) if landing_languages() else {}
    written = []
    for lang in langs:
        if lang not in report:
            fail(f"unknown language {lang!r}; known: {', '.join(report)}")
        work = {"lang": lang, "rules": RULES, "glossary": glossary(catalogs["app"], lang)}
        for section in CATALOGS:
            work[section] = {key: work_item(key, catalogs[section][key], lang, issues)
                             for key, issues in report[lang][section].items()}
        work["landing"] = landing_work_items(lang, report[lang]["landing"], landing_en)
        count = sum(len(work[section]) for section in [*CATALOGS, "landing"])
        if not count:
            continue
        path = Path(args.out) / f"{lang}.json" if args.out else WORK_DIR / f"{lang}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(work, indent=2, ensure_ascii=False) + "\n", "utf8")
        written.append(f"{companion.rel(path, ROOT)} ({count})")
    print("\n".join(written) if written else "nothing to translate")
    return 0


def work_item(key: str, entry: dict, lang: str, issues: list[str]) -> dict:
    """A catalog key's item in a work file: its English and comment, the plural categories and substitutions to fill,
    the current translation, the issues and an empty `value`."""
    english = english_of(key, entry)
    current, _ = translation_of(entry, lang)
    categories = categories_for(lang)
    item = {"en": english}
    if entry.get("comment"):
        item["comment"] = entry["comment"]
    if subs := english_substitutions(entry):
        item["substitutions"] = {
            name: {"en": sub["plural"], "categories": categories, "value": {cat: "" for cat in categories}}
            for name, sub in subs.items()
        }
    elif isinstance(english, dict):
        item["categories"] = categories
    elif isinstance(english, str) and INT_SPEC_RE.search(english) and not current:
        # New keys with a count: the English plural variants may still need creating.
        item["en_plural"] = {"one": "", "other": english}
        item["categories"] = categories
    if current:
        item["current"] = current
    item["issues"] = issues
    item["value"] = {cat: "" for cat in categories} if isinstance(english, dict) else ""
    return item


def landing_work_items(lang: str, report: dict[str, list[str]], landing_en: dict[str, object]) -> dict[str, dict]:
    """The landing page's items in a work file (extra keys are reported by status; nothing to translate)."""
    exists = LANDING_I18N is not None and (LANDING_I18N / f"{lang}.json").exists()
    current = flatten(load_landing(lang)) if exists else {}
    items = {}
    for key, issues in report.items():
        if key in landing_en:
            items[key] = {"en": landing_en[key], **({"current": current[key]} if current.get(key) else {}),
                          "issues": issues, "value": ""}
    return items


def set_catalog_value(entry: dict, lang: str, value) -> None:
    localizations = entry.setdefault("localizations", {})
    if isinstance(value, dict):
        plural = {cat: {"stringUnit": {"state": "translated", "value": value[cat]}} for cat in sorted(value)}
        localizations[lang] = {"variations": {"plural": plural}}
    else:
        localizations[lang] = {"stringUnit": {"state": "translated", "value": value}}
    ordered = sorted(localizations.items())
    localizations.clear()
    localizations.update(ordered)


def cmd_apply(args) -> int:
    files = [Path(p) for pattern in args.files for p in (glob.glob(pattern) or [pattern])]
    catalogs = {section: load_catalog(path) for section, path in CATALOGS.items()}
    landing_en_tree = load_landing(SOURCE) if landing_languages() else {}
    landing_trees: dict[str, dict] = {}
    errors, applied, skipped = [], 0, 0
    dirty = set()
    for file in files:
        work = json.loads(file.read_text("utf8"))
        lang = work["lang"]
        for section in CATALOGS:
            strings = catalogs[section]["strings"]
            for key, item in work.get(section, {}).items():
                value = item.get("value")
                if value in ("", None) or (isinstance(value, dict) and not any(value.values())):
                    skipped += 1
                elif key not in strings:
                    errors.append(f"{file.name} [{section}] {key!r}: key no longer in catalog")
                elif problems := validate_item(key, item, strings[key], lang):
                    errors.append(f"{file.name} [{section}] {key!r}: {'; '.join(problems)}")
                else:
                    apply_item(strings[key], key, item, lang)
                    dirty.add(section)
                    applied += 1
        landing_applied, landing_skipped = apply_landing(work, file.name, landing_en_tree, landing_trees, errors)
        applied, skipped = applied + landing_applied, skipped + landing_skipped
    for section in dirty:
        dump_catalog(CATALOGS[section], catalogs[section])
    for lang, tree in landing_trees.items():
        dump_landing(lang, tree)
    print(f"applied {applied}, skipped {skipped} empty, rejected {len(errors)}")
    for error in errors:
        print(f"  {error}")
    return 1 if errors else 0


def promoted_plural(key: str, item: dict) -> dict | None:
    """The English plural a work item fills in (`en_plural.one`, for a count that needs singular and plural forms),
    which becomes the key's English when its translation goes in; None without one."""
    en_plural = item.get("en_plural") or {}
    if en_plural.get("one") and isinstance(item.get("value"), dict):
        return {"one": en_plural["one"], "other": en_plural.get("other") or key}
    return None


def validate_item(key: str, item: dict, entry: dict, lang: str) -> list[str]:
    """Why a filled work item can't go into its catalog `entry` in `lang`: none when it can."""
    value = item["value"]
    english, english_subs = english_of(key, entry), english_substitutions(entry)
    if promoted := promoted_plural(key, item):
        if problem := token_problem(key, promoted["one"]):
            return [f"en_plural.one: {problem}"]
        english, english_subs = promoted, {}  # the source becomes a plain plural, as apply_item writes it
    if isinstance(english, dict):
        if not isinstance(value, dict):
            return ["plural needs an object value"]
        if missing := [cat for cat in categories_for(lang) if not value.get(cat)]:
            return [f"missing plural {', '.join(missing)}"]
        reference = english.get("other", next(iter(english.values())))
        problems = [f"{cat}: {p}" for cat, text in value.items() if (p := token_problem(reference, text))]
    elif not isinstance(value, str):
        problems = ["value must be a string"]
    else:
        problems = [p] if (p := token_problem(english, value)) else []
    given = item.get("substitutions") or {}
    for name in english_subs:
        sub_value = (given.get(name) or {}).get("value") or {}
        if missing := [cat for cat in categories_for(lang) if not sub_value.get(cat)]:
            problems.append(f"substitution {name}: missing {', '.join(missing)}")
        problems += [f"substitution {name} {cat}: needs %arg" for cat, text in sub_value.items() if text and "%arg" not in text]
    return problems


def apply_item(entry: dict, key: str, item: dict, lang: str) -> None:
    """Writes a validated work item into its catalog `entry`: the English plural it fills in, the translation and its
    substitutions' plural forms."""
    if promoted := promoted_plural(key, item):
        set_catalog_value(entry, SOURCE, promoted)
    english_subs = english_substitutions(entry)
    set_catalog_value(entry, lang, item["value"])
    if english_subs:
        given = item["substitutions"]
        entry["localizations"][lang]["substitutions"] = {
            name: {
                "argNum": sub["argNum"],
                "formatSpecifier": sub["formatSpecifier"],
                "variations": {"plural": {
                    cat: {"stringUnit": {"state": "translated", "value": given[name]["value"][cat]}}
                    for cat in sorted(categories_for(lang))
                }},
            }
            for name, sub in english_subs.items()
        }


def apply_landing(work: dict, name: str, english_tree: dict, trees: dict[str, dict], errors: list[str]) -> tuple[int, int]:
    """Validates a work file's landing items and sets the good ones in `trees` (per language, loaded on first use),
    adding the problems to `errors`; how many went in and how many were empty."""
    english = flatten(english_tree)
    applied = skipped = 0
    for key, item in work.get("landing", {}).items():
        value = item.get("value")
        if not value:
            skipped += 1
        elif key not in english:
            errors.append(f"{name} [landing] {key!r}: key not in en.json")
        elif problem := token_problem(str(english[key]), str(value)):
            errors.append(f"{name} [landing] {key!r}: {problem}")
        else:
            set_path(trees.setdefault(work["lang"], load_landing(work["lang"])), key, value, english_tree)
            applied += 1
    return applied, skipped


def cmd_add(args) -> int:
    """Adds strings that only the Windows/Linux ports use (`add_strings`), from a JSON file, and prints what it did."""
    return print_added(*add_strings(json.loads(Path(args.file).read_text("utf8"))))


def add_strings(new: dict) -> tuple[list[str], list[str]]:
    """Adds strings that only the Windows/Linux ports use. Xcode never extracts them, so they are `manual` entries:
    `sync` keeps them and `prune` leaves them alone. `new` maps a key to {"comment", "en"} or {"comment", "plural"}; a
    key already in the catalog with the same English is left alone. The keys added and the problems."""
    path = CATALOGS["app"]
    data = load_catalog(path)
    strings = data["strings"]
    added, errors = [], []
    for key, item in new.items():
        english = item.get("plural") or item.get("en") or key
        if key in strings:
            if english_of(key, strings[key]) != english:
                errors.append(f"{key!r}: already in the catalog with different English")
            continue
        reference = english.get("other", "") if isinstance(english, dict) else english
        if problem := token_problem(key, reference):
            errors.append(f"{key!r}: {problem}")
            continue
        entry = {"comment": item["comment"]} if item.get("comment") else {}
        entry["extractionState"] = "manual"
        set_catalog_value(entry, SOURCE, english)
        strings[key] = entry
        added.append(key)
    if added:
        data["strings"] = dict(sorted(strings.items()))
        dump_catalog(path, data)
    return added, errors


def print_added(added: list[str], errors: list[str]) -> int:
    """`add_strings`' result for people; 1 when something was rejected."""
    print(f"added {len(added)}, rejected {len(errors)}")
    for key in added:
        print(f"  + {key!r}")
    for error in errors:
        print(f"  {error}")
    print("next: `l10n.py export` to translate them, then `export-windows` / `export-linux`")
    return 1 if errors else 0


def cmd_prune(args) -> int:
    removed = 0
    for section, path in CATALOGS.items():
        data = load_catalog(path)
        stale = [key for key, entry in data["strings"].items() if entry.get("extractionState") == "stale"]
        for key in stale:
            del data["strings"][key]
            print(f"removed {key!r} from {path.name}")
        if stale:
            dump_catalog(path, data)
        removed += len(stale)
    print(f"removed {removed} stale keys")
    return 0


def cmd_sync(args) -> int:
    catalog = CATALOGS["app"]
    before = set(load_catalog(catalog)["strings"])
    scheme = CONFIG.require("mac.scheme")
    # An app with an iPhone and iPad target builds that too: strings only it shows (`#if os(iOS)`) would
    # otherwise be marked stale, and `prune` would drop them.
    builds = [(scheme, ["-destination", "platform=macOS"])]
    if ios := CONFIG.get("mac.iosScheme"):
        builds.append((ios, ["-destination", "generic/platform=iOS Simulator", "CODE_SIGNING_ALLOWED=NO"]))
    log_path = WORK_DIR.parent / "l10n-build.log"
    if not args.no_build:
        print(f"building {', '.join(name for name, _ in builds)} (output in {log_path.relative_to(ROOT)})…", flush=True)
        WORK_DIR.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as log:
            for name, options in builds:
                build = subprocess.run(
                    ["xcodebuild", "-project", CONFIG.require("mac.project"), "-scheme", name, *options,
                     "-derivedDataPath", str(DERIVED), "build", *companion.xcode_cache_settings()],
                    cwd=MACOS, stdout=log, stderr=subprocess.STDOUT,
                )
                if build.returncode != 0:
                    break
        if build.returncode != 0:
            tail = log_path.read_text("utf8", "replace").splitlines()
            print("\n".join(line for line in tail if "error:" in line)[-4000:] or "\n".join(tail[-30:]))
            fail("build failed")
    markers = [CONFIG.get("l10n.stringsdataFilter")] if CONFIG.get("l10n.stringsdataFilter") else [
        f"{name}.build" for name, _ in builds]
    stringsdata = [p for p in DERIVED.rglob("*.stringsdata")
                   if any(marker in str(p) for marker in markers) and "Tests" not in p.name]
    if not stringsdata:
        fail(f"no .stringsdata under {DERIVED}; build first (drop --no-build)")
    result = subprocess.run(
        ["xcrun", "xcstringstool", "sync", str(catalog), "--stringsdata", *map(str, stringsdata)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout + result.stderr)
        fail("xcstringstool sync failed")
    data = load_catalog(catalog)
    after = set(data["strings"])
    added = sorted(after - before)
    stale = [key for key, entry in data["strings"].items() if entry.get("extractionState") == "stale"]
    print(f"synced {catalog.name}: {len(added)} new, {len(stale)} stale")
    for key in added:
        print(f"  + {key!r}")
    print("next: `l10n.py status`, then `l10n.py export` if anything is missing")
    return 0


# ------------------------------------------------------------------------ Windows and Linux exports
#
# Localizable.xcstrings stays the only source. The ports get generated files with .NET-style
# `{0}` placeholders instead of Apple's specifiers:
#
#   <windows.strings>/<lang>/Resources.resw   name = resw_name(key); plurals as name__one, name__few, name__other …
#   <l10n.linuxPo>/<lang>.po + LINGUAS         msgid = the catalog key; plurals use gettext plural forms
#
# The ports look strings up by the catalog key and format them with the arguments in order: `{0}` is the first
# argument, `%2$@` becomes `{1}`. A language-scoped phrase's keys are left out of the other languages' files.

# gettext Plural-Forms and the CLDR category behind each index.
PO_PLURALS = {
    "one-other": ("nplurals=2; plural=(n != 1);", ["one", "other"]),
    "fr": ("nplurals=2; plural=(n > 1);", ["one", "other"]),
    "ro": ("nplurals=3; plural=(n==1 ? 0 : (((n%100>19) || ((n%100==0) && (n!=0))) ? 2 : 1));",
           ["one", "few", "other"]),
    "pl": ("nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
           ["one", "few", "many"]),
    "cs": ("nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;", ["one", "few", "other"]),
    "none": ("nplurals=1; plural=0;", ["other"]),
}
PO_PLURAL_KIND = {
    "cs": "cs",
    "fr": "fr",
    "ja": "none",
    "ko": "none",
    "pl": "pl",
    "pt-BR": "fr",
    "ro": "ro",
    "zh-Hans": "none",
}

SUBST_RE = re.compile(r"%#@([A-Za-z0-9_]+)@")
CLDR_ORDER = ["zero", "one", "two", "few", "many", "other"]


def cldr_sorted(plural: dict) -> dict:
    return {cat: plural[cat] for cat in sorted(plural, key=CLDR_ORDER.index)}


def po_plurals(lang: str) -> tuple[str, list[str]]:
    return PO_PLURALS[PO_PLURAL_KIND.get(lang, "one-other")]


def resw_name(key: str) -> str:
    """A .resw-safe name for a catalog key: lower-case ASCII letters and digits stay, every other
    UTF-16 code unit becomes `_` plus 4 upper-case hex digits (so `_` itself is `_005F`).
    Upper-case letters are escaped too because MRT resource names ignore case ("To" vs "to").
    MakePri silently drops names over 255 characters, so a name over RESW_NAME_MAX keeps its first
    RESW_NAME_PREFIX characters plus `_h` and 16 hex digits of the key's SHA-256 (escapes use
    upper-case hex, so `_h` never clashes); that leaves room for a `__other` plural suffix."""
    out = []
    for ch in key:
        if ch.isascii() and (ch.islower() or ch.isdigit()):
            out.append(ch)
        else:
            units = ch.encode("utf-16-be")
            out.extend(f"_{int.from_bytes(units[i:i + 2], 'big'):04X}" for i in range(0, len(units), 2))
    name = "".join(out)
    if len(name) > RESW_NAME_MAX:
        name = name[:RESW_NAME_PREFIX] + "_h" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return name


RESW_NAME_MAX = 240
RESW_NAME_PREFIX = 200


def to_net_format(text: str, substitutions: dict | None = None, variant: str | None = None) -> str:
    """Apple format → .NET composite format. `%lld`/`%@` become `{n}` in order, `%2$@` becomes
    `{1}`, `%%` becomes `%`, braces are doubled. A `%#@name@` substitution is replaced by its
    `variant` (a plural category) with `%arg` as the substitution's argument."""
    out, index, pos = [], 0, 0
    pattern = re.compile(SUBST_RE.pattern + "|" + SPEC_RE.pattern)
    for m in pattern.finditer(text):
        out.append(text[pos:m.start()].replace("{", "{{").replace("}", "}}"))
        pos = m.end()
        token = m.group(0)
        if token == "%%":
            out.append("%")
        elif m.group(1):
            sub = (substitutions or {}).get(m.group(1))
            if sub is None or variant is None:
                fail(f"unresolved substitution {token} in {text!r}")
            arg = sub.get("argNum", index + 1) - 1
            index = max(index, arg + 1)
            units = sub["variations"]["plural"]
            unit = units.get(variant) or units["other"]
            inner = unit["stringUnit"]["value"].replace("{", "{{").replace("}", "}}")
            out.append(re.sub(r"%arg", "{%d}" % arg, inner))
        elif m.group(2):
            out.append("{%d}" % (int(m.group(2)) - 1))
        else:
            out.append("{%d}" % index)
            index += 1
    out.append(text[pos:].replace("{", "{{").replace("}", "}}"))
    return "".join(out)


def export_units(lang: str) -> list[dict]:
    """Every app string for `lang` (English where it's missing), in key order:
    {key, comment, value} or {key, comment, plural: {category: value}, countArg}."""
    catalog = load_catalog(CATALOGS["app"])
    units = []
    for key in sorted(catalog["strings"]):
        entry = catalog["strings"][key]
        if entry.get("shouldTranslate") is False or entry.get("extractionState") == "stale":
            continue
        if not in_scope(key, lang):
            continue
        loc = entry.get("localizations", {})
        chosen = loc.get(lang) if lang in loc else None
        if chosen is None or (translation_of(entry, lang)[0] in (None, "", {})):
            chosen = loc.get(SOURCE, {"stringUnit": {"value": key}})
        comment = entry.get("comment", "")
        if "variations" in chosen:
            plural = {cat: to_net_format(u["stringUnit"]["value"])
                      for cat, u in chosen["variations"].get("plural", {}).items()}
            units.append({"key": key, "comment": comment, "plural": cldr_sorted(plural), "countArg": 0})
            continue
        value = chosen.get("stringUnit", {}).get("value", key)
        subs = chosen.get("substitutions")
        names = SUBST_RE.findall(value)
        if not names:
            units.append({"key": key, "comment": comment, "value": to_net_format(value)})
            continue
        if len(names) > 1 or not subs:
            fail(f"{key!r} ({lang}): only one plural substitution per string is supported")
        sub = subs[names[0]]
        categories = list(sub["variations"]["plural"])
        plural = {cat: to_net_format(value, subs, cat) for cat in categories}
        units.append({"key": key, "comment": comment, "plural": cldr_sorted(plural),
                      "countArg": sub.get("argNum", 1) - 1})
    return units


def xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


RESW_HEADER = """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated from {catalog} by laching/tools/l10n.py export-windows. Don't edit. -->
<root>
  <resheader name="resmimetype">
    <value>text/microsoft-resx</value>
  </resheader>
  <resheader name="version">
    <value>2.0</value>
  </resheader>
  <resheader name="reader">
    <value>System.Resources.ResXResourceReader, System.Windows.Forms, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</value>
  </resheader>
  <resheader name="writer">
    <value>System.Resources.ResXResourceWriter, System.Windows.Forms, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</value>
  </resheader>
"""


# Plural variants are separate resw names, `name__one`. `#` can't be used: MRT treats it as a URI
# fragment. An escaped name never contains `__` (every `_` starts a 4-digit escape).
PLURAL_SEP = "__"


def plural_categories() -> dict[str, list[str]]:
    """Every plural category any language uses, per key. Each .resw lists all of them (a language
    repeats its `other` text for categories it doesn't use): MakePri needs a default (English)
    value for every name, or packaging fails."""
    found: dict[str, set[str]] = {}
    for lang in export_languages():
        for unit in export_units(lang):
            if "plural" in unit:
                found.setdefault(unit["key"], set()).update(unit["plural"])
    return {key: sorted(cats, key=CLDR_ORDER.index) for key, cats in found.items()}


def resw_text(lang: str, categories: dict[str, list[str]] | None = None) -> str:
    categories = plural_categories() if categories is None else categories
    lines = [RESW_HEADER.replace("{catalog}", catalog_label())]
    for unit in export_units(lang):
        name = resw_name(unit["key"])
        base = f"Key: {unit['key']}" + (f" | {unit['comment']}" if unit["comment"] else "")
        plural = unit.get("plural", {})
        values = ([(f"{name}{PLURAL_SEP}{cat}", plural.get(cat, plural["other"]),
                    f"{base} | plural {cat}, count is {{{unit['countArg']}}}")
                   for cat in categories[unit["key"]]] if plural
                  else [(name, unit["value"], base)])
        for n, text, comment in values:
            lines.append(f'  <data name="{xml_escape(n)}" xml:space="preserve">\n'
                         f"    <value>{xml_escape(text)}</value>\n"
                         f"    <comment>{xml_escape(comment)}</comment>\n"
                         "  </data>\n")
    lines.append("</root>\n")
    return "".join(lines)


def po_quote(text: str) -> str:
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
               .replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{escaped}"'


def po_text(lang: str) -> str:
    forms, categories = po_plurals(lang)
    code = lang.replace("-", "_")
    out = [f"# Generated from {catalog_label()} by laching/tools/l10n.py export-linux. Don't edit.",
           'msgid ""', 'msgstr ""', po_quote(f"Language: {code}\n"), po_quote("MIME-Version: 1.0\n"),
           po_quote("Content-Type: text/plain; charset=UTF-8\n"), po_quote("Content-Transfer-Encoding: 8bit\n"),
           po_quote(f"Plural-Forms: {forms}\n"), ""]
    for unit in export_units(lang):
        if unit["comment"]:
            out += [f"#. {line}" for line in unit["comment"].splitlines()]
        if "plural" in unit:
            out.append(f"#. plural count is {{{unit['countArg']}}}")
            out += [f"msgid {po_quote(unit['key'])}", f"msgid_plural {po_quote(unit['key'])}"]
            for i, cat in enumerate(categories):
                text = unit["plural"].get(cat) or unit["plural"]["other"]
                out.append(f"msgstr[{i}] {po_quote(text)}")
        else:
            out += [f"msgid {po_quote(unit['key'])}", f"msgstr {po_quote(unit['value'])}"]
        out.append("")
    return "\n".join(out)


def export_languages() -> list[str]:
    return [SOURCE] + app_languages()


def catalog_label() -> str:
    """The catalog's path from the app's root, for the generated files' headers."""
    return CATALOGS["app"].relative_to(ROOT).as_posix()


def windows_files(root: Path | None = None) -> dict[Path, str]:
    root = root or WINDOWS_STRINGS
    if root is None:
        fail("companion.json has no windows.strings: pass --out")
    categories = plural_categories()
    return {root / lang / "Resources.resw": resw_text(lang, categories) for lang in export_languages()}


def linux_files(root: Path | None = None) -> dict[Path, str]:
    root = root or LINUX_PO
    if root is None:
        fail("companion.json has no l10n.linuxPo: pass --out")
    langs = export_languages()
    files = {root / f"{lang.replace('-', '_')}.po": po_text(lang) for lang in langs}
    files[root / "LINGUAS"] = "\n".join(lang.replace("-", "_") for lang in langs) + "\n"
    return files


def android_name(key: str) -> str:
    """An Android resource name for a catalog key: `s_` plus `resw_name(key).lower()`, so every
    name starts with a lowercase ASCII letter, uses only `[a-z0-9_]`, and never collides with a
    Java or Kotlin keyword."""
    return "s_" + resw_name(key).lower()


def android_locale(lang: str) -> str:
    """The locale tag `locales_config.xml` lists for a catalog language: what the language's values folder says
    (zh-Hans → zh-CN, as `values-zh-rCN`), the rest as they are."""
    return "zh-CN" if lang == "zh-Hans" else lang


def android_values_dir(lang: str) -> str:
    if lang == SOURCE:
        return "values"
    if lang == "zh-Hans":
        return "values-zh-rCN"
    if "-" in lang:
        base, region = lang.split("-", 1)
        return f"values-{base}-r{region}"
    return f"values-{lang}"


def android_escape(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("\r", "")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("'", "\\'")
        .replace('"', '\\"')
    )
    if escaped.startswith(("@", "?")):
        escaped = "\\" + escaped
    if escaped.startswith(" "):
        escaped = "\\u0020" + escaped[1:]
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\u0020"
    return xml_escape(escaped)


ANDROID_HEADER = """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated from {catalog} by laching/tools/l10n.py export-android. Don't edit. -->
<resources xmlns:tools="http://schemas.android.com/tools" tools:ignore="MissingTranslation">
"""


def android_text(lang: str, categories: dict[str, list[str]] | None = None) -> str:
    categories = plural_categories() if categories is None else categories
    lines = [ANDROID_HEADER.replace("{catalog}", catalog_label())]
    for unit in export_units(lang):
        name = android_name(unit["key"])
        plural = unit.get("plural", {})
        values = (
            [(f"{name}{PLURAL_SEP}{cat}", plural.get(cat, plural["other"])) for cat in categories[unit["key"]]]
            if plural
            else [(name, unit["value"])]
        )
        for n, text in values:
            lines.append(f'  <string name="{xml_escape(n)}" formatted="false">{android_escape(text)}</string>\n')
    lines.append("</resources>\n")
    return "".join(lines)


def android_locales_text() -> str:
    """`res/xml/locales_config.xml` (the manifest's `android:localeConfig`): the languages the system's per-app language
    setting offers, one per exported language, so the list never drifts from the values folders."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>\n',
        f"<!-- Generated from {catalog_label()} by laching/tools/l10n.py export-android. Don't edit. -->\n",
        '<locale-config xmlns:android="http://schemas.android.com/apk/res/android">\n',
    ]
    lines += [f'  <locale android:name="{android_locale(lang)}"/>\n' for lang in export_languages()]
    lines.append("</locale-config>\n")
    return "".join(lines)


def android_files(root: Path | None = None) -> dict[Path, str]:
    """`values*/strings.xml` per language and `xml/locales_config.xml`, under the app module's res folder."""
    root = root or ANDROID_STRINGS
    if root is None:
        fail("companion.json has no android.strings: pass --out")
    categories = plural_categories()
    files = {root / android_values_dir(lang) / "strings.xml": android_text(lang, categories) for lang in export_languages()}
    files[root / "xml" / "locales_config.xml"] = android_locales_text()
    return files


def stale_exports() -> list[Path]:
    """Generated files that differ from the catalog. Only checked once the port's folder exists."""
    stale = []
    ports = [(WINDOWS_STRINGS.parents[1], windows_files)] if WINDOWS_STRINGS else []
    ports += [(LINUX_PO.parent, linux_files)] if LINUX_PO else []
    if ANDROID_STRINGS:  # the Gradle project's folder, or four up from <app>/src/main/res without `android.dir`
        ports.append((ANDROID_DIR or ANDROID_STRINGS.parents[min(3, len(ANDROID_STRINGS.parents) - 1)], android_files))
    for folder, files in ports:
        if folder.exists():
            stale += companion.write_outputs(files(), check=True)
    return stale


def write_export(files: dict[Path, str], dry_run: bool) -> int:
    """Writes the files that differ from the catalog (LF on every OS: the exports are committed as-is, -text, and CI
    diffs them byte for byte); with `dry_run`, lists them."""
    for path in companion.write_outputs(files, check=dry_run):
        print(f"would write {path} ({len(files[path].encode('utf8'))} bytes)" if dry_run else f"wrote {path}")
    return 0


def cmd_export_windows(args) -> int:
    return write_export(windows_files(Path(args.out) if args.out else None), args.dry_run)


def cmd_export_linux(args) -> int:
    return write_export(linux_files(Path(args.out) if args.out else None), args.dry_run)


def cmd_export_android(args) -> int:
    return write_export(android_files(Path(args.out) if args.out else None), args.dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="build and sync new/stale keys into the String Catalog")
    sync.add_argument("--no-build", action="store_true", help="reuse the last build's .stringsdata")
    sync.set_defaults(func=cmd_sync)

    for name, func, help_text in (
        ("status", cmd_status, "summarize translation gaps"),
        ("check", cmd_check, "like status, but exit 1 on any gap"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("-v", "--verbose", action="store_true", help="list every problem")
        cmd.add_argument("--since", metavar="REF", help="also flag strings whose English changed since a git ref "
                         "(the app's; a submodule at the commit it records)")
        cmd.set_defaults(func=func)

    export = sub.add_parser("export", help="write per-language work files with only the strings to translate")
    export.add_argument("--lang", action="append", help="language code (repeatable); default: all with gaps")
    export.add_argument("--since", metavar="REF", help="also re-export strings whose English changed since a git ref "
                        "(the app's; a submodule at the commit it records)")
    export.add_argument("--out", help="output directory (default <mac.dir>/build/l10n)")
    export.set_defaults(func=cmd_export)

    apply = sub.add_parser("apply", help="merge filled-in work files back after validating them")
    apply.add_argument("files", nargs="+")
    apply.set_defaults(func=cmd_apply)

    prune = sub.add_parser("prune", help="remove keys Xcode marked stale")
    prune.set_defaults(func=cmd_prune)

    add = sub.add_parser("add", help="add strings only the Windows/Linux ports use (manual catalog entries)")
    add.add_argument("file", help='JSON: {"<key>": {"comment": "...", "en": "..."} or {"comment": ..., "plural": {"one": ..., "other": ...}}}')
    add.set_defaults(func=cmd_add)

    for name, func, default, help_text in (
        ("export-windows", cmd_export_windows, "windows.strings", "write .resw files for the Windows port"),
        ("export-linux", cmd_export_linux, "l10n.linuxPo", "write .po files for the Linux port"),
        ("export-android", cmd_export_android, "android.strings",
         "write values*/strings.xml and xml/locales_config.xml for the Android app"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--out", help=f"output directory (default: {default} in companion.json)")
        cmd.add_argument("--dry-run", action="store_true", help="list the files without writing them")
        cmd.set_defaults(func=func)

    args = parser.parse_args()
    configure()
    return args.func(args)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
