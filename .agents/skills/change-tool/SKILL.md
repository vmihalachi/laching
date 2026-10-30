---
name: change-tool
description: Use when adding or changing a tool in tools/ (a Python tool, a shell script, a hook, the store or Windows kit) or a companion.json key it reads. Covers the five places that move together (the tool, tools/tests, tools/README.md, companion.schema.json, tools/tests/covering.py), the no-app-names rule and how the apps get the change.
---

# Add or change a tool

`tools/` serves every companion app, which vendors this repo as a `laching/` submodule at a release. A tool reads
everything app-specific from the app's `companion.json` (through `tools/companion.py`), never names an app and ships
no app's strings. Change these together, in one pull request:

| Place | What changes |
|---|---|
| The tool (`tools/<name>.py`, `.sh`, `tools/store/`, `tools/windows/`, `tools/hooks/hook.py`) | The behavior, and its docstring's usage lines at the top (the commands and flags, one per line). |
| `tools/companion.schema.json` | A new or changed key, with its `type`, `description` and default. `companion.py validate` checks every app against it, and the schema is the source of truth for every section. |
| `tools/tests/test_<name>.py` | A test for the change, over `tools/tests/fixture-app/` (add the key there when the tool needs it) or a temporary folder. Tests never reach the network. |
| `tools/tests/covering.py` | A new file gets a `COVERS` entry naming the tests that exercise it, or `test_covering` fails; a file every test leans on goes in `SHARED`. |
| `tools/README.md` | The tool's line in the table and what the README says about it: commands, keys, what it never does. |

## Rules

- **Read config through `companion.py`:** `config = companion.load()`, then `config.get("a.b")` or
  `config.path("a.b")` (an absolute path, or `None`). Paths in `companion.json` are relative to the app's root.
- **No app names.** `test_scripts.SharedFilesTests.test_no_app_is_named` reads every file under `tools/` outside the
  tests; app names, bundle ids and copy come from `companion.json`.
- **Standard library only**, unless the tool's docstring names the extra (as `fonts.py` does fontTools), and Python
  3.10 or later.
- **A new key's default keeps every app working unchanged**: the apps update their `companion.json` when they move
  to the release, not before.
- **One copy.** An app calls the tool from its submodule (`python3 laching/tools/<name>.py`) or through a thin
  wrapper; `companion.py conform` fails on a copy, so a new shared file it should guard goes in `tools/conform.py`.
- A skill that runs the tool (`tools/skills/`, which the apps symlink) says the new command or flag.

## Verify

```sh
python3 tools/tests/run.py <name>                  # the tool's own tests; a word filters by file name
python3 tools/tests/run.py --covering tools/<file>  # which tests verify.sh --fast will run for it
python3 tools/tests/run.py                          # all of them, side by side
scripts/verify.sh                                   # what applies to your changes
```

The apps get the change at the next release and their bump (the `release` skill); before that, an app can try it with
`companion.py link ../laching`. Name the change in the pull request's title for the release notes, and label it
`breaking` when an app has to change its `companion.json` or its wrappers.
