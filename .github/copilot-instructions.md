# Copilot instructions for Laching

All agent guidance lives in [`AGENTS.md`](../AGENTS.md) at the repo root: where things live, the commands, the
conventions and the skills; the full rules are in [`docs/conventions.md`](../docs/conventions.md). Read it before
changing code. It isn't duplicated here.

The rules that matter most, for tools that only read this file (such as code review):

- **Public API is deliberate:** every public type gets a doc comment saying what it does, what it needs and what it
  never does.
- **Package code never names an app:** no app's skins, phrase bank, product ids, palette, voices or strings, and no app
  name in a doc comment or fixture; apps inject them. `tools/` reads everything app-specific from `companion.json`.
- **No permission prompts, no network** in the packages.
- **Conformance vectors are generated, never edited by hand:** `conformance/` changes only through the Swift code and
  `scripts/conformance.sh`.
- **Behavior changes are additive and vector-tested:** a config field whose default keeps every vector byte-identical,
  a unit test, a vector case and the C# port (and the Kotlin one, for the scheduler), all in the same change.
- **C# mirrors Swift** folder for folder and file for file (the deviations are in `docs/layout.md`), with
  source-generated JSON only: the apps publish with Native AOT.
