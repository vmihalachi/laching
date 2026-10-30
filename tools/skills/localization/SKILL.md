---
name: localization
description: Add, change or translate user-facing text in a Laching companion app (the String Catalogs in companion.json `l10n.catalog`/`l10n.plist`, the landing page's i18n files) without loading the catalog into context. Use whenever a change adds or edits UI strings, the character's phrases or jokes, notifications or landing copy, or when asked to translate, fix translations or add a language.
---

# Localization workflow

The app's `Localizable.xcstrings` grows with every phrase and language; reading or editing it directly makes every
later turn expensive. Use `laching/tools/l10n.py` (every path and language comes from `companion.json`): it prints
short summaries and writes small work files. App specifics (the character's voice, the glossary, who reviews which
language) are in the app's `DESIGN.md` and `.agents/notes/localization.md` when it exists: read them first.

## Rules

- Never `view`, `cat` or `grep -A/-C` the `.xcstrings` files, and never dump whole landing i18n files into context
  (the hook refuses). To look up one key, `grep -n '"<key>"' <file>` without context lines, or
  `python3 laching/tools/l10n.py status -v`.
- Never hand-edit translations into the catalogs. Go through `export` → fill → `apply`, which validates format
  specifiers, `{placeholders}`, HTML tags and plural categories (`l10n.pluralCategories`).
- **Transcreate, never translate literally**, everywhere: app, phrases, landing, every language. A calque is a bug
  even when it's accurate. The work files carry the generic rules plus the app's (`l10n.rulesFile`) and the names to
  keep (`l10n.keepNames`); keep the rules file current when the voice changes.
- **Language-scoped phrases.** A phrase listed for some languages (`languages` in the bank) exists only there: its
  keys are required in those languages and skipped everywhere else (`status`, `check`, `export`, `export-windows`).
  Its source text is in its own language (an Italian-only joke is written in Italian).
- Recorded voices say exactly the catalog text: a changed line is *pending* in every voiced language until it's
  re-recorded (`voice` skill); users never hear it meanwhile.

## Steps

1. **Write the code** with source-language strings as usual. Phrases and jokes: `add-phrase` skill
   (`phrases.py sync` adds their keys). Landing copy: add the key to the source language's file only.
2. **Sync**: `python3 laching/tools/l10n.py sync` builds and adds new keys to the catalog. Skip it if the app
   didn't change.
3. **See the gaps**: `python3 laching/tools/l10n.py status`. If source text changed under an existing key, add
   `--since HEAD` (or the base ref). Stale keys the code no longer uses: `prune`.
4. **Export and fan out**: `python3 laching/tools/l10n.py export [--since HEAD]` writes
   `<mac.dir>/build/l10n/<lang>.json`, one per language with gaps. Launch one background sub-agent per file, all in
   the same turn (a fast, cheaper model is fine). Prompt for each:

   > Translate the work file `<path>` for <displayName> (<one line about the character from DESIGN.md>). Read the file,
   > follow its `rules` and `glossary` (above all: transcreate, write it as a native copywriter would, never word for
   > word), fill every `value` (and `en_plural.one` where relevant), and save it in place as JSON. Don't touch any
   > other file and don't open `.xcstrings` files. Reply with one line: the number of strings you filled and anything
   > you were unsure about.

   Don't read the work files yourself; wait for the one-line replies.
5. **Apply**: `python3 laching/tools/l10n.py apply '<mac.dir>/build/l10n/*.json'`. If it rejects entries, send the
   error lines back to that language's sub-agent and apply again.
6. **Verify**: `python3 laching/tools/l10n.py check` must print `ok` for every language. It also fails when the Windows
   `.resw`, Linux `.po` or Android `strings.xml` exports are stale: run `l10n.py export-windows` (or `export-linux`,
   `export-android`) and commit the output. Build the app (or run the tests); when landing copy changed, build the
   landing page.
7. **Report**: list the new or changed strings in the languages the owner reviews (`.agents/notes/localization.md`),
   source → translation, one line each, and anything the sub-agents flagged as unsure.

## Adding a language

Add the code to `languages.all` in `companion.json` and to `CFBundleLocalizations` in the XcodeGen spec (then
`xcodegen generate`); add it to the landing page's language list and create its empty i18n file. If it needs plural
categories other than one/other, add them to `l10n.pluralCategories`. Then steps 3–7: `export` lists every string.
