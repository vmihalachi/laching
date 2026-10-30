# 6: iOS platform, docs, site, changelog, the M9 dry run, `v0.5.0`

> Sequence 6 of `README.md`. Needs 1–5. One session, macOS. Use the `release` skill for the version mechanics.
>
> From sequence 3 (done 2026-10-11, branch `0.5-dotnet`): `Laching.Companion` and `Laching.Testing` exist, pack, and
> replay every `conformance/companion` vector (`dotnet/Laching.Companion.Tests`); `release.yml` packs both beside
> `Laching.Core`. C# names that differ from the Swift: `FlavorId`/`SkinId`, `PartContent.Vector` (Swift `.shapes`),
> `IDrawableCharacter`, `Transform.Then` (`concatenating`), `PhraseTiming.PartBeatSeconds`/`PartDelaySeconds`, a
> `TimeZoneInfo` where Swift takes a `Calendar`; no `VoiceId` yet (Swift's `VoiceID` arrives in Shell with sequence 4).
> The owner confirmed (2026-10-11) that the NuGet Trusted Publishing policy and the `Laching.` prefix reservation cover
> `Laching.Companion` and `Laching.Testing`.
>
> From sequence 4 (done 2026-10-11, branch `0.5-shell-ui`): `LachingShell` has `StatusItemController` and `Speech/`;
> `LachingCompanionUI` exists (it also depends on `LachingStore`, for `ScreenshotCapture`'s `FixedPriceBackend`). The
> palette, `Space` and the speech bubble landed in 4.2 with the views that draw them. For 6.1: every file in
> `LachingSystem`, `LachingShell`, `LachingStore`, `LachingCompanionUI` and their test targets (41 sources) already
> starts with `#if os(macOS)`, so the iOS step is `platforms` plus the `swift.yml` simulator build. `CharacterView`
> draws image parts through `Bundle.image(forResource:)` (SwiftUI's `Image(_:bundle:)` draws nothing in a `Canvas` for
> a dotted asset name); on iOS that will need `UIImage(named:in:with:)`.
>
> From sequence 5 (done 2026-10-11, branch `0.5-tools`): `tools/` is complete (`tools/README.md` has the adoption
> steps for sunnysays' M9 and Pagliacci's M0). `companion.json` gained a few optional keys over the README's list:
> `l10n.{phraseComment,flavorNotes,stringsdataFilter}`, a structured `fonts` section and
> `screenshots.store.{font,noteFont,palette}`. A draft of sunnysays' file is `tools/tests/example.companion.json`;
> both it and `../pagliacci/companion.json` validate. Not run end to end here: `fonts.py` (needs fontTools and a
> download) and `store-screenshots.py` (needs Pillow); their tests skip without them.

## Read only these

- `README.md` here (the changelog list at the end); `.agents/skills/release/SKILL.md`; `CHANGELOG.md`; `README.md` (root); `AGENTS.md`;
  `PRODUCT.md`; `site/index.html` (the version label and install snippets only: grep `exact:` and `class="version"`); `scripts/verify.sh`;
  `.github/workflows/swift.yml`; `Package.swift`.
- From sunnysays: `plans/milestones/9-laching-companion.md` (steps 2–5 are the dry run).

## Steps

| Step | Commit | What |
|---|---|---|
| 6.1 | `iOS: Core and Companion build for iOS 17` | `platforms: [.macOS(.v14), .iOS(.v17)]`; every file in `LachingSystem`, `LachingShell`, `LachingStore`, `LachingCompanionUI` and their tests wrapped in `#if os(macOS)` (sequence 4's list); `swift.yml` step `xcodebuild -scheme LachingCompanion -destination 'generic/platform=iOS Simulator' build`. **Droppable:** if the wrapping drags past an hour, revert this step, note it in the CHANGELOG as 0.6 work, and go on. |
| 6.2 | `docs: 0.5 in README, AGENTS, PRODUCT, site` | Root README "What's inside" rows (Companion, CompanionUI, Testing, tools) and the *Using it* notes (profile, skins, sequences, languages); `AGENTS.md` layout table (`Sources/LachingCompanion`, `LachingCompanionUI`, `dotnet/Laching.Companion`, `Laching.Testing`, `tools/`, `plans/`), conventions ("package code never names an app", the raster rule, `conformance/companion`); `PRODUCT.md` (the store is on both platforms since 0.3.0; the companion layer); `site/index.html` sections and the "Used by" line. |
| 6.3 | `0.5.0: the companion layer` | CHANGELOG: move *Unreleased* under `## 0.5.0 - <date>` with the breaking list and migrations from the README; bump the four version places; `scripts/verify.sh --all`. Do **not** tag yet. |
| 6.4 | (sunnysays branch, no Laching commit) | **M9 dry run:** on a sunnysays branch `m9-dry-run`, point `macos/SunnySaysCore/Package.swift` and `macos/project.yml` at `path: ../laching` and the csproj at a local feed (`dotnet pack dotnet -o /tmp/laching-feed` + `nuget.config`), then do M9's steps 2–5 (core, Windows core, Mac app, tools) far enough to run Gate A (vectors byte-identical) and Gate C (screenshots identical). Any API that bites gets fixed here in Laching (amend 6.3's commit or add a fix commit). Leave the branch for the real M9 to reuse. |
| 6.5 | tag | Owner tags `v0.5.0` (or dispatches `release.yml` with the version). Confirm NuGet shows `Laching.Core`, `Laching.Companion`, `Laching.Testing`, `Laching.Windows` 0.5.0 and that `.package(url:…, exact: "0.5.0")` resolves in a scratch package. |

## Finish

`scripts/verify.sh --all` ok; `swift.yml`, `dotnet.yml`, `release.yml` green; the dry run's gates passed (say which
API changes it forced). sunnysays' M9 and Pagliacci's M0 can start.

## Outcome (2026-10-11, branch `0.5-release`)

- **6.1 dropped** at the owner's call: Laching stays desktop-only (phone apps would get a package of their own), so
  `platforms` is macOS 14 and there is no iOS Simulator build. The wrapping had worked (the whole package built for
  the iOS Simulator); the CHANGELOG doesn't mention iOS.
- **6.2, 6.3** done: docs, `0.5.0: the companion layer` (not tagged), `scripts/verify.sh --all` ok.
- **6.4** on sunnysays' `m9-dry-run` (two commits; pins point at `../laching` and a local NuGet pack, so it is not for
  master as is):
  - Gate A: `phrases.json`, `picker/*` and `quiet/*` byte-identical. Two format changes, checked equal in meaning:
    `settings.json` gains `recentWindow`, `partBeatSeconds`, `reappearDelaySeconds`, `hourHideSeconds` (the
    Windows replay requires them; the values match `phrases.json` and the retired `desktop.json`), and
    `sunflower-poses.json` writes `headExtras`/`overlays` lists where it had `cheeks`/`zz` (identical once mapped).
    `cadence/` and `desktop.json` deleted.
  - Gate B: `SunnySays.Core.Tests` 57 green on the packed 0.5.0 (147 before; the mechanism tests moved here).
    `SunnySays.App` not ported (Windows only).
  - Gate C: 24/24 screenshots pixel-identical to the pre-migration build, once both hold the character's still pose;
    before that, two runs of the *same* build differed wherever the character swayed.
  - Forced in Laching (`M9 dry run: …` commit): `CharacterView` holds the still pose during a `ScreenshotCapture`
    run; `tools/character.py` reads `companion.json` without `--design`; the sunnysays draft's
    `voice.siteCopyright` fixed. No Swift or C# API changed.
  - Tools checked against sunnysays' data: `phrases.py`, `character.py --check` clean; `l10n.py` and `voice.py` differ
    only in generated headers and the review site's generic markup (M9 commits the regenerated files); `synth.py`
    waits for M9's `macos/scripts/sounds.py`.
- **6.5** waits for the owner: push and merge, CI, then the tag.
