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
                                                      # merge filled work files, then regenerate the ports' exports
  python3 laching/tools/l10n.py prune                # delete keys Xcode marked stale
  python3 laching/tools/l10n.py add FILE.json        # add strings only the Windows/Linux ports use
  python3 laching/tools/l10n.py check                # exit 1 if anything is missing or invalid,
                                                      # or the Windows/Linux/Android exports are stale
  python3 laching/tools/l10n.py export-windows [--out DIR] [--dry-run]
                                                      # .resw per language for the Windows port
  python3 laching/tools/l10n.py export-linux [--out DIR] [--dry-run]
                                                      # .po per language for the Linux port
  python3 laching/tools/l10n.py export-android [--out DIR] [--dry-run]
                                                      # strings.xml per language and locales_config.xml for Android

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

`apply` then regenerates the exports made from the catalog, as `export-windows`, `export-linux` and `export-android`
do, for each port `companion.json` names (`windows.strings`, `l10n.linuxPo`, `android.strings`) and whose folder
exists: the files `check` compares, so `check` passes straight after it. When an export fails, `apply` says why and
that the catalog was written, and exits 2. `export` ends with the batch's size, the distinct strings in the files it
wrote.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
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
    "%2$@). A percent sign is `%%` where `en` has a format specifier, a plural or a `%#@name@` token (and so in "
    "every substitution form), and a plain `%` where it has none: write it as `en` does. For plurals, `value` is an object with every key in `categories`, each including the number. If an "
    "item has `en_plural`, decide whether the count needs singular/plural forms: if it does, fill `en_plural.one` "
    "(English singular, same specifiers) and give `value` as a plural object; otherwise leave `en_plural` alone and "
    "give a string. If an item has `substitutions`, its `en` holds a `%#@name@` token that stands for a counted "
    "phrase: keep the token in `value`, and for each name fill `substitutions.<name>.value` with every key in "
    "`categories`, each containing `%arg` where the number goes (see `substitutions.<name>.en`) and any other "
    "specifier exactly as `substitutions.<name>.en` writes it, position included (%2$lld stays %2$lld)."
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
SUBST_RE = re.compile(r"%(?:\d+\$)?#@([A-Za-z0-9_]+)@")  # `%#@name@`, or with Apple's compiled position, `%1$#@name@`
# A token in a string: a substitution (group 1, its name) or a format specifier (groups 2 and 3, SPEC_RE's).
VALUE_TOKEN_RE = re.compile(SUBST_RE.pattern + "|" + SPEC_RE.pattern)
BRACE_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
# The conversions that print a number, and of those the integers (a plural counts one of these).
NUMBER_CONVERSIONS = set("dDiuUoOxXfFeEgGaA")
INTEGER_CONVERSIONS = set("dDiuUoOxX")
# A `%` with printf's space flag, as "100% done" reads to printf. Apple formats a string only when the call passes it
# arguments, which the source decides for every language (`formats`): in a string that takes none ("100% on-device, 0%
# data sent") every `%` is text, and in one that takes some this is a specifier (`%lld % fatto` prints
# "5 0,000000atto"), so a percent sign there is `%%`.
LITERAL_PERCENT_RE = re.compile(r"%(?:\d+\$)?[-+#0]* ")
# What Swift writes for an interpolation, never a percent sign even right after a number (`Top 10\(name)`); Text's
# `specifier:` writes flags, width or precision too (`Room 1\(n, specifier: "%02d")`), which a word never does.
SWIFT_SPECIFIERS = {"%@", "%lld", "%llu", "%ld", "%lu", "%lf", "%d", "%u", "%f"}
BARE_SPEC_RE = re.compile(r"%(?:hh|h|ll|l|q|z|t|j)?.")  # a `%`, a length and a conversion: what `50%OFF` makes


def percent_sign(text: str, m: re.Match) -> bool:
    """Whether a SPEC_RE match in `text` is a percent sign, should the string take no arguments: `%%`, a `%` before a
    space, or a bare one right after a digit ("100%ige", "50%OFF") that Swift wouldn't write."""
    token = m.group(0)
    return (m.group(2) == "%" or bool(LITERAL_PERCENT_RE.match(token))
            or (m.start() > 0 and text[m.start() - 1].isdigit() and token not in SWIFT_SPECIFIERS
                and BARE_SPEC_RE.fullmatch(token) is not None))


def plain(text: str) -> bool:
    """Whether `text` reads as a string that takes no arguments: no substitution, and nothing that looks like a
    specifier but a percent sign (`percent_sign`). Apple prints such a string as it is, `%%` included."""
    return not SUBST_RE.search(text) and all(percent_sign(text, m) for m in SPEC_RE.finditer(text))


def formats(entry: dict, key: str = "") -> bool | None:
    """Whether Apple formats a catalog entry's strings, in every language: the call passes arguments when the source
    takes them (Swift writes an interpolation as a specifier and then a percent sign as `%%`, and a literal without one
    as it is), and a plural form or a substitution is always formatted. None without a source text to tell."""
    loc = entry.get("localizations", {}).get(SOURCE, {})
    if "variations" in loc or loc.get("substitutions"):
        return True
    english = loc.get("stringUnit", {}).get("value") or key
    return not plain(english) if english else None


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


def arguments(text: str) -> list[tuple[int, str, bool]]:
    """The arguments `text`'s format specifiers take, numbered as Apple counts them: (number, specifier, whether it
    gives its position). A positionless one takes the one after the last positionless one, whatever the others take;
    `%%` takes none, and neither does a `%#@name@` substitution here, since its argNum comes from the catalog, nor
    anything in a `plain` string ("100% on-device, 0% data sent"), which Apple doesn't format."""
    out, count = [], 0
    if plain(text):
        return out
    for m in SPEC_RE.finditer(text):
        token = m.group(0)
        if m.group(2) == "%" or (m.group(2) == "@" and "#" in token):
            continue
        if m.group(1):
            out.append((int(m.group(1)), token, True))
        else:
            count += 1
            out.append((count, token, False))
    return out


def numbers(text: str) -> dict[int, str]:
    """The arguments `text` prints as numbers, each once however often it's printed: {Apple's number: conversion}."""
    return {number: token[-1] for number, token, _ in arguments(text) if token[-1] in NUMBER_CONVERSIONS}


def specifiers(text: str) -> list[str]:
    """The format specifiers in `text`, each with the argument it takes: a positionless one is numbered as Apple
    counts it (`%@ moved %lld` holds `%1$@` and `%2$lld`), so a translation may reorder arguments with positions but
    never hand one to another specifier. A `%#@name@` substitution is `%#@`: its argNum comes from the catalog, and
    Apple's count skips it."""
    out = ["%#@" for m in SPEC_RE.finditer(text) if m.group(2) == "@" and "#" in m.group(0)]
    out += [token if positioned else f"%{number}${token[1:]}" for number, token, positioned in arguments(text)]
    return sorted(out)


def markup(text: str) -> list[str]:
    return sorted(BRACE_RE.findall(text) + TAG_RE.findall(text))


def token_problem(english: str, translated: str) -> str | None:
    """Why a translation's format specifiers, placeholders or tags don't match its English, or None."""
    if specifiers(english) != specifiers(translated):
        # A specifier that would be a percent sign in a string without arguments: "% d", "100%ige".
        percent = not plain(english) and any(
            m.group(2) != "%" and percent_sign(translated, m) for m in SPEC_RE.finditer(translated))
        hint = ": a percent sign beside a number is %%" if percent else ""
        return f"format specifiers {specifiers(english)} != {specifiers(translated)}{hint}"
    return markup_problem(english, translated)


def markup_problem(english: str, translated: str) -> str | None:
    """Why a translation's {placeholders} and HTML tags don't match its English, or None: all a landing string has,
    since its `%` is never printf's."""
    if markup(english) != markup(translated):
        return f"placeholders/tags {markup(english)} != {markup(translated)}"
    return None


# A token in a substitution's plural form: its `%arg`, or a format specifier (with SPEC_RE's groups).
FORM_TOKEN_RE = re.compile(r"%arg|" + SPEC_RE.pattern)


def form_specifiers(form: str) -> list[str]:
    """The format specifiers in a substitution's plural form besides `%arg`, positions kept: the form is spliced into
    its string, so a number there names its argument (`%2$lld`) and a translation can't renumber it."""
    return sorted(m.group(0) for m in FORM_TOKEN_RE.finditer(form) if m.group(0) != "%arg" and m.group(2) != "%")


def form_number_problem(form: str) -> str | None:
    """Why the ports can't place a number in a substitution's plural form, or None: a substitution inside it, or a
    number without its position (Apple splices the form into its string, so which argument that one takes isn't the
    form's to say)."""
    if nested := SUBST_RE.search(form):
        return f"holds another substitution, {nested.group(0)}, which the ports can't take"
    if why := percent_problem(form, True, form=True):
        return why
    tokens = FORM_TOKEN_RE.finditer(form)
    if loose := [m.group(0) for m in tokens if m.group(0) != "%arg" and m.group(2) != "%" and not m.group(1)]:
        return f"{', '.join(loose)} needs its position, as %2$lld"
    return None


def percent_problem(text: str, formatted: bool | None = None, form: bool = False) -> str | None:
    """Why a `%` in `text` doesn't print as written, or None. Whether Apple formats `text` is the caller's to say
    (`formats`, the same for every language of an entry, and always for a plural form or a substitution's `form`), or
    else `text`'s own (`plain`).
    - Formatted, a percent sign is `%%`: a `%` before a space is a specifier with printf's space flag and prints an
      argument that isn't there (`%lld % fatto` prints "5 0,000000atto"), one that starts no specifier is dropped, at
      times with the letter after it (`%lld % neu` prints "5 eu", `%lld %` "5 "), one right after a number reads an
      argument too, where Swift wouldn't write it (`50%OFF` prints "505FF"), and a percent sign with printf's flags or
      width prints what follows its first `%` (`%5%` prints "5%").
    - Not formatted, Apple prints `%%` as it is, twice."""
    if formatted is None:
        formatted = not plain(text)
    if not formatted:
        return "Apple prints a string that takes no arguments as it is, so %% shows twice: write one %" if "%%" in text else None
    pos = 0
    for m in (FORM_TOKEN_RE if form else VALUE_TOKEN_RE).finditer(text):
        if (stray := text.find("%", pos, m.start())) >= 0:
            break
        pos, token = m.end(), m.group(0)
        if token == "%arg" or token == "%%" or SUBST_RE.fullmatch(token):
            continue
        spec = SPEC_RE.match(text, m.start())
        if spec.group(2) == "%":
            return f"{token!r} prints as {token[1:]!r}: write the percent sign as %%"
        if LITERAL_PERCENT_RE.match(token):
            return (f"{token!r} is a specifier to Apple, beside the string's arguments, and prints one that isn't "
                    "there: write the percent sign as %%")
        if percent_sign(text, spec):
            return f"{token!r} after a number is a specifier to Apple, not a percent sign: write the percent sign as %%"
    else:
        stray = text.find("%", pos)
    if stray >= 0:
        return (f"{text[stray:stray + 3]!r}: Apple drops a % that starts no specifier, at times with the letter after "
                "it: write the percent sign as %%")
    return None


def form_problem(english: str, translated: str) -> str | None:
    """Why a substitution's translated plural form doesn't match its English one (`other`): none when it does."""
    if not any(m.group(0) == "%arg" for m in FORM_TOKEN_RE.finditer(translated)):
        return "needs %arg"
    if form_specifiers(english) != form_specifiers(translated):
        return f"format specifiers {form_specifiers(english)} != {form_specifiers(translated)}"
    if problem := form_number_problem(translated):
        return problem
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
            "argNum": sub.get("argNum"),
            "formatSpecifier": sub.get("formatSpecifier"),
            "plural": {cat: unit["stringUnit"]["value"] for cat, unit in sub["variations"]["plural"].items()},
        }
        for name, sub in subs.items()
    }


def english_form(sub: dict) -> str:
    """The English plural form a substitution's translations are checked against: its `other`."""
    return sub["plural"].get("other", next(iter(sub["plural"].values())))


def substitution_issues(entry: dict, lang: str) -> list[str]:
    """Each source substitution must have every plural category `lang` uses, filled in, each form with `%arg` and the
    English form's other specifiers, and count the argument the English counts, as the same type."""
    english = english_substitutions(entry)
    if not english:
        return []
    subs = entry.get("localizations", {}).get(lang, {}).get("substitutions", {})
    issues = []
    for name, sub in english.items():
        if name in subs:
            issues += [f"substitution {name}: {field} {subs[name].get(field)!r} != {sub[field]!r}"
                       for field in ("argNum", "formatSpecifier") if subs[name].get(field) != sub[field]]
        plural = subs.get(name, {}).get("variations", {}).get("plural", {})
        forms = {cat: unit.get("stringUnit", {}).get("value") for cat, unit in plural.items()}
        missing = [cat for cat in categories_for(lang) if not forms.get(cat)]
        if missing:
            issues.append(f"substitution {name}: missing {', '.join(missing)}")
        issues += [f"substitution {name} {cat}: {problem}" for cat, form in forms.items()
                   if form and (problem := form_problem(english_form(sub), form))]
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
        elif isinstance(text, str) and (problem := markup_problem(text, str(translated[key]))):
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


def plural_problem(forms) -> str | None:
    """Why Xcode can't take a plural variation with these forms, or None. It reads every form together: over two
    numbers it can't tell which one counts ("Cannot reliably infer argument number…", positions or not, even with one
    in each form), and a number spelled two ways (`%lld` and `%ld`, `%lld:%02lld`, a whole number and a fraction) fails
    the build; the same spelling twice only warns. A substitution says which number counts."""
    spellings: dict[int, set[str]] = {}
    for form in forms:
        for number, token, positioned in arguments(form):
            if token[-1] in NUMBER_CONVERSIONS:
                spellings.setdefault(number, set()).add("%" + token.partition("$")[2] if positioned else token)
    spelled = sorted(set().union(*spellings.values()))
    if len(spellings) > 1:
        fails = f", and spelled two ways ({', '.join(spelled)}) it fails the build" if len(spelled) > 1 else ""
        return (f"a plural over {len(spellings)} numbers, so Xcode can't tell which one counts{fails}: count it through "
                "a substitution, %#@name@")
    if len(spelled) > 1:
        return f"a plural spelling its number two ways, {' and '.join(spelled)}, which fails Xcode's build: spell it one way"
    return None


def placement_problem(value: str, subs: dict[str, dict]) -> str | None:
    """Why Apple can't place a string's numbers around its substitutions ({name: {argNum, formatSpecifier, …}}), or
    None:
    - one without an argNum or a formatSpecifier, which Xcode always writes (without an argNum Apple takes the next
      argument and the ports can't tell which; without a formatSpecifier the build fails);
    - `%N$#@name@` at another argument than its argNum: Apple picks the plural form by argument N but prints argNum's;
    - a number without a position that takes a substitution's argument: Apple numbers positionless specifiers without
      counting the substitution, so `%#@hours@, %lld min` prints the hours twice."""
    for name, sub in subs.items():
        if sub.get("argNum") is None:
            return f"%#@{name}@ has no argNum: give it the number of the argument it counts, as Xcode writes it"
        if not sub.get("formatSpecifier"):
            return (f"%#@{name}@ has no formatSpecifier: give it the conversion of the number it counts, as lld, as "
                    "Xcode writes it")
    counts = {name: sub["argNum"] for name, sub in subs.items()}
    for m in re.finditer(r"%(\d+)\$#@([A-Za-z0-9_]+)@", value):
        position, name = int(m.group(1)), m.group(2)
        if counts.get(name, position) != position:
            return (f"{m.group(0)} picks its plural form by argument {position} but prints argument {counts[name]}, "
                    "its argNum: give it the same position, or none")
    counted = {number: name for name, number in counts.items()}
    for number, token, positioned in arguments(value):
        if not positioned and number in counted:
            return (f"{token} takes argument {number}, which %#@{counted[number]}@ counts, as Apple numbers it: give it "
                    "the position it means, as %2$lld")
    return None


def number_problem(entry: dict, key: str = "") -> str | None:
    """Why Apple can't place a number or a percent sign in a catalog entry, in any language, or None: a `%` that
    doesn't print as written (`percent_problem`), a plural over two numbers (`plural_problem`), a number misplaced
    around a substitution (`placement_problem`), or one a substitution's form doesn't place (`form_number_problem`).
    The source language is read first, so a mistake every translation copies is named where it's made."""
    localizations = entry.get("localizations", {})
    formatted = formats(entry, key)
    if SOURCE not in localizations and (why := percent_problem(key, formatted)):  # the key is the English
        return f"{SOURCE} {key!r}: {why}"
    for lang in sorted(localizations, key=lambda code: code != SOURCE):
        loc = localizations[lang]
        units = loc.get("variations", {}).get("plural", {}).values()
        forms = [unit.get("stringUnit", {}).get("value", "") for unit in units]
        value = loc.get("stringUnit", {}).get("value", "")
        subs = loc.get("substitutions", {})
        checks = [(form, True) for form in forms] + [(value, True if subs else formatted)]
        for text, formatted_here in checks:
            if why := percent_problem(text, formatted_here):
                return f"{lang} {text!r}: {why}"
        if why := plural_problem(forms):
            return f"{lang} {' / '.join(repr(form) for form in dict.fromkeys(forms))} is {why}"
        if why := placement_problem(value, subs):
            return f"{lang} {value!r}: {why}"
        for name, sub in subs.items():
            for cat, unit in sub.get("variations", {}).get("plural", {}).items():
                if why := form_number_problem(form := unit.get("stringUnit", {}).get("value", "")):
                    return f"{lang} substitution {name} {cat} {form!r}: {why}"
    return None


def number_problems() -> dict[str, dict[str, str]]:
    """Every catalog's entries with a number or a percent sign Apple can't place (`number_problem`), each with why."""
    return {
        section: {key: why for key, entry in load_catalog(path)["strings"].items()
                  if (why := number_problem(entry, key))}
        for section, path in CATALOGS.items()
    }


def cmd_status(args) -> int:
    return report(args, strict=False)


def cmd_check(args) -> int:
    result = report(args, strict=True)
    stale = stale_exports()
    for path in stale:
        print(f"stale export: {path.relative_to(ROOT).as_posix()} (run {EXPORT_COMMANDS})")
    return 1 if stale else result


def report(args, strict: bool) -> int:
    """The gaps per language (one line per problem with `args.verbose`), the stale keys and the numbers Apple can't
    place (each with why); with `strict`, 1 on any."""
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
    for section, problems in number_problems().items():
        if problems:
            total += len(problems)
            print(f"numbers Apple can't place in {CATALOGS[section].name}: {len(problems)}")
            for key, why in problems.items():
                print(f"  {key!r}: {why}")
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
    batch: set[tuple[str, str]] = set()  # every string some file asks for, once however many languages lack it
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
        batch.update((section, key) for section in [*CATALOGS, "landing"] for key in work[section])
        path = Path(args.out) / f"{lang}.json" if args.out else WORK_DIR / f"{lang}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(work, indent=2, ensure_ascii=False) + "\n", "utf8")
        written.append(f"{companion.rel(path, ROOT)} ({count})")
    if not written:
        print("nothing to translate")
        return 0
    print("\n".join(written))
    # The batch's size, which decides who translates it (the localization skill): one translator, or one per file.
    print(f"{counted(len(batch), 'distinct string')} in {counted(len(written), 'file')}")
    return 0


def counted(count: int, noun: str) -> str:
    """"1 file", "3 files"."""
    return f"{count} {noun}{'' if count == 1 else 's'}"


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
    elif isinstance(english, str) and not current and len(counted := numbers(english)) == 1 \
            and set(counted.values()) <= INTEGER_CONVERSIONS and not plural_problem([english]):
        # New keys with a count: the English plural variants may still need creating. Not with two numbers, or one
        # spelled two ways: Xcode can't take that plural (`plural_problem`), so it takes a substitution, made in the
        # catalog.
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
    if not export_after_apply(catalog_written=bool(dirty)):
        return 2
    return 1 if errors else 0


def export_after_apply(catalog_written: bool) -> bool:
    """Brings the ports' exports in line with the catalog `apply` has written (`regenerate_exports`) and prints where it
    wrote, so `check` passes straight after `apply`. False when an export fails, after saying why and that the catalog
    was written: its translations are in, and the exports stay stale until an export command passes. A failure nobody
    foresaw says the same and keeps its traceback."""
    sys.stdout.flush()  # the summary first, when it shares a pipe with the errors
    try:
        lines = regenerate_exports()
    except BaseException as error:
        if isinstance(error, OSError):
            print(f"error: {error}", file=sys.stderr)
        catalog = "the catalog was written" if catalog_written else "the catalog was left as it was"
        print(f"error: {catalog}, but the exports made from it are stale: fix that, then run {EXPORT_COMMANDS}",
              file=sys.stderr)
        # `fail` has said why (a string no port can take), or the line above has; anything else keeps its traceback.
        if isinstance(error, (SystemExit, OSError)):
            return False
        raise
    for line in lines:
        print(line)
    return True


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
        if problem := (token_problem(key, promoted["one"]) or percent_problem(promoted["one"], True)
                       or plural_problem(promoted.values())):
            return [f"en_plural.one: {problem}"]
        english, english_subs = promoted, {}  # the source becomes a plain plural, as apply_item writes it
    if isinstance(english, dict):
        if not isinstance(value, dict):
            return ["plural needs an object value"]
        if missing := [cat for cat in categories_for(lang) if not value.get(cat)]:
            return [f"missing plural {', '.join(missing)}"]
        reference = english.get("other", next(iter(english.values())))
        problems = [f"{cat}: {p}" for cat, text in value.items()
                    if (p := token_problem(reference, text) or percent_problem(text, True))]
        if not problems and (why := plural_problem(value.values())):
            problems.append(why)
    elif not isinstance(value, str):
        problems = ["value must be a string"]
    else:
        problems = [p] if (p := token_problem(english, value) or percent_problem(value, formats(entry, key))) else []
        if english_subs and (why := placement_problem(value, english_subs)):
            problems.append(why)
    given = item.get("substitutions") or {}
    for name, sub in english_subs.items():
        sub_value = (given.get(name) or {}).get("value") or {}
        if missing := [cat for cat in categories_for(lang) if not sub_value.get(cat)]:
            problems.append(f"substitution {name}: missing {', '.join(missing)}")
        problems += [f"substitution {name} {cat}: {p}" for cat, text in sub_value.items()
                     if text and (p := form_problem(english_form(sub), text))]
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
        elif problem := markup_problem(str(english[key]), str(value)):
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
        if isinstance(english, dict):  # a percent sign first: a `%i` it makes reads as another number
            problem = (token_problem(key, english.get("other", ""))
                       or next((why for form in english.values() if (why := percent_problem(form, True))), None)
                       or plural_problem(english.values()))
        else:
            problem = token_problem(key, english) or percent_problem(english)
        if problem:
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
    print("next: `l10n.py export` to translate them, then `apply`, which regenerates the ports' exports")
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


def form_to_net(form: str, arg: int, text: str) -> str:
    """A substitution's plural form → .NET composite format: `%arg` becomes `{arg}`, a number with its position
    (`%2$lld`) `{1}`, `%%` `%`, braces are doubled. A number without a position fails: Apple splices the form into
    `text`, so which argument it would take isn't the form's to say; so does a substitution inside the form."""
    if nested := SUBST_RE.search(form):
        fail(f"the substitution form {form!r} of {text!r} holds another substitution, {nested.group(0)}, "
             "which the ports can't take")
    out, pos = [], 0
    for m in FORM_TOKEN_RE.finditer(form):
        out.append(form[pos:m.start()].replace("{", "{{").replace("}", "}}"))
        pos = m.end()
        token = m.group(0)
        if token == "%arg":
            out.append("{%d}" % arg)
        elif m.group(2) == "%":  # `%%` prints `%`, and with printf's flags what follows its first `%`, as on Apple
            out.append(token[1:])
        elif m.group(1):
            out.append("{%d}" % (int(m.group(1)) - 1))
        else:
            fail(f"{token} in the substitution form {form!r} of {text!r} needs its position, as %2$lld")
    out.append(form[pos:].replace("{", "{{").replace("}", "}}"))
    return "".join(out)


def to_net_format(text: str, substitutions: dict | None = None, variant: str | None = None,
                  literal: bool | None = None) -> str:
    """Apple format → .NET composite format. `%lld`/`%@` become `{n}` in order, `%2$@` becomes
    `{1}`, `%%` becomes `%`, braces are doubled. A `%#@name@` substitution is replaced by its
    `variant` (a plural category), converted by `form_to_net`. A `literal` string, one Apple doesn't format (`formats`;
    left out, `plain`), keeps its `%`s as Apple prints it."""
    out, index, pos = [], 0, 0
    literal = plain(text) if literal is None else literal
    for m in VALUE_TOKEN_RE.finditer(text):
        out.append(text[pos:m.start()].replace("{", "{{").replace("}", "}}"))
        pos = m.end()
        token = m.group(0)
        if literal:
            out.append(token)
        elif m.group(3) == "%":  # `%%` prints `%`, and with printf's flags what follows its first `%`, as on Apple
            out.append(token[1:])
        elif m.group(1):
            sub = (substitutions or {}).get(m.group(1))
            if sub is None or variant is None:
                fail(f"unresolved substitution {token} in {text!r}")
            # Its argNum says which argument it counts; Apple's positionless count goes on past it, so
            # `noch %#@left@, %lld fertig` (left counting argument 2) prints argument 1 at `%lld`.
            if "argNum" not in sub:
                fail(f"the substitution {token} in {text!r} has no argNum: give it the number of the argument it "
                     "counts, as Xcode writes it")
            arg = sub["argNum"] - 1
            units = sub["variations"]["plural"]
            unit = units.get(variant) or units["other"]
            out.append(form_to_net(unit["stringUnit"]["value"], arg, text))
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
        if "variations" in chosen:  # Apple formats every plural form, even one without the number
            plural = {cat: to_net_format(u["stringUnit"]["value"], literal=False)
                      for cat, u in chosen["variations"].get("plural", {}).items()}
            units.append({"key": key, "comment": comment, "plural": cldr_sorted(plural), "countArg": 0})
            continue
        value = chosen.get("stringUnit", {}).get("value", key)
        subs = chosen.get("substitutions")
        names = SUBST_RE.findall(value)
        if not names:  # formatted or not as the source is, in every language
            units.append({"key": key, "comment": comment,
                          "value": to_net_format(value, literal=not formats(entry, key))})
            continue
        if len(names) > 1 or not subs:
            fail(f"{key!r} ({lang}): only one plural substitution per string is supported")
        sub = subs[names[0]]
        categories = list(sub["variations"]["plural"])
        plural = {cat: to_net_format(value, subs, cat, literal=False) for cat in categories}
        units.append({"key": key, "comment": comment, "plural": cldr_sorted(plural),
                      "countArg": sub["argNum"] - 1})
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


EXPORT_COMMANDS = "`export-windows` / `export-linux` / `export-android`"


def export_ports() -> list[tuple[Path, Callable[[], dict[Path, str]]]]:
    """The ports whose exports follow the catalog, as (where its files go, what makes them): each one `companion.json`
    names, once the port's folder exists."""
    ports = [(WINDOWS_STRINGS.parents[1], WINDOWS_STRINGS, windows_files)] if WINDOWS_STRINGS else []
    ports += [(LINUX_PO.parent, LINUX_PO, linux_files)] if LINUX_PO else []
    if ANDROID_STRINGS:  # the Gradle project's folder, or four up from <app>/src/main/res without `android.dir`
        folder = ANDROID_DIR or ANDROID_STRINGS.parents[min(3, len(ANDROID_STRINGS.parents) - 1)]
        ports.append((folder, ANDROID_STRINGS, android_files))
    return [(root, files) for folder, root, files in ports if folder.exists()]


def stale_exports() -> list[Path]:
    """Generated files that differ from the catalog. Only checked once the port's folder exists."""
    return [path for _, files in export_ports() for path in companion.write_outputs(files(), check=True)]


def regenerate_exports() -> list[str]:
    """Rewrites the generated files that differ from the catalog, in the ports `check` compares (`export_ports`); a line
    for each port it wrote in. Every port's files are made before the first is written, so a string no port can take
    (`fail`) leaves them all as they were."""
    made = [(root, files()) for root, files in export_ports()]
    lines = []
    for root, files in made:
        if written := companion.write_outputs(files, check=False):
            where = companion.rel(root, ROOT)
            lines.append(f"regenerated {len(written)} of {counted(len(files), 'export')} in {where}")
    return lines


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

    apply = sub.add_parser("apply", help="merge filled-in work files back after validating them, then regenerate the "
                           "ports' exports")
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
