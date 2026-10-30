---
name: review-invariants
description: "Review a diff in a Laching companion app against the rules tests don't catch: privacy (no camera or microphone, no network, nothing persisted beyond settings), stable identifiers, pure logic staying pure, localized text, palette tokens, Windows and Android parity, generated files, concurrency, plus the app's own appendix. Use before committing a change to the Mac app, the Windows port, the Android app or the landing page, or when asked to review against the project rules. Read-only; returns findings with file:line."
---

# Review against the invariants

Scope: what you were given (default: working tree vs HEAD; or a commit range or file list). An agent definition may
run this in its own context; any agent can also follow it inline.

You review a diff against `checklist.md` (next to this file) and the app's appendix, `.agents/review-appendix.md` when
it exists (its items continue the numbering: monetization rules, brand words to avoid, the app's palette tokens…). You
don't edit files and you don't run git commands that change anything (no checkout, restore, stash, reset, add, commit):
other sessions may share this checkout. Paths and names come from `companion.json`.

## Steps

1. Read `checklist.md` and the app's appendix.
2. Get the diff for the scope. Default: `git diff HEAD --stat` and `git ls-files --others --exclude-standard` (new
   files), then `git diff HEAD -- <path>` per file, and Read new code files. For a range: `git diff <range>`. Never
   read `*.xcstrings`, `*.resw` or `project.pbxproj` (a hook blocks it): `--stat` tells you they changed; for strings
   run `python3 laching/tools/l10n.py check`.
3. Go through every item the changed paths could touch, with the checks the items name (grep the added lines, read
   the surrounding code). An added line is only a finding if it breaks the rule in context: `Date()` in a view is
   fine, in the core package it isn't.
4. Windows and Android parity: compare user-visible Mac changes (`hooks.macUI` in companion.json, strings) with changes
   under the Windows app (`hooks.windowsUI`) and, when `hooks.androidUI` is set, the Android app. A missing port is a
   finding unless the change has no counterpart there.
5. Pinned logic changed: check the vectors (`conformance.dir`), the Windows core and, when the app has an `android`
   section, the Kotlin core (`android.core`) changed too.

## Report

Return at most this, most severe first:

```
Scope: <what you reviewed, N files>
Findings:
- [#<item>] <path>:<line> — <what breaks the rule, one sentence> → <the fix, one sentence>
- ...
Checked, nothing found: <item numbers>
Not applicable: <item numbers>
```

A diff that only touches tooling or docs → `Scope: tooling/docs only, no app impact` plus any generated-files finding.
No findings → say so in one line. No style advice, praise or rules the diff doesn't touch. If unsure, list it under
Findings with "(unsure)" and why.
