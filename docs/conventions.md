# Conventions, in full

The complete rules (moved from `AGENTS.md`, which keeps the short form).

- **Public API is deliberate.** Every public type gets a doc comment saying what it does, what it needs and what it
  never does (for example "never prompts for a permission"). Keep type names that mybackhurts already uses
  (`ReminderScheduler`, `SchedulerConfig`, `Clock`, `BusyStateProvider`…) so adopting the package is an import swap.
- **Swift 6 language mode** (strict concurrency; public value types are `Sendable`), macOS 14+, iOS 17+ (Swift), Windows 11 (.NET), Android 8+ (Kotlin), no dependencies. `LachingCore` stays Foundation-only and `LachingCompanion`
  Foundation and Observation only (both build on Linux); anything that imports AppKit, SwiftUI, CoreAudio, AVFoundation,
  IOKit or Carbon goes in `LachingSystem`, `LachingShell` or `LachingCompanionUI`.
- **Classes an app drives are `@MainActor`; the protocols injected into them aren't `Sendable`.**
  `ReminderScheduler`, `CompanionCoordinator` and `CompanionSettings` (and so an app's subclass of it), like the UI's
  models and watchers, are isolated to the main actor, so a call from a background queue is a compile error rather
  than a data race. `Clock`, `IdleTimeProvider`, `BusyStateProvider`, `RandomSource` and
  `PowerStateProvider` stay non-`Sendable`: the class that holds one calls it from its own actor, so a mock or an
  app's provider needs no lock. The conformance generators that run those classes, and the test classes that call them, are
  `@MainActor` too, with `async` test methods: Linux's XCTest can't run a synchronous main-actor test.
- **Package code never names an app.** No app's skins, phrase bank, product ids, palette or voices, and no app name in
  a doc comment or a fixture: apps inject their bank, defaults, skins, palette and voices. The same goes for `tools/`,
  which reads everything app-specific from the app's `companion.json`.
- **A view or model in the package takes its words from the app.** `LachingCompanionUI`'s shell, Settings rows,
  Support window and menus get every label as a `Text`, a string or a copy struct (`StatusCopy`, `MenuCopy`,
  `SupportCopy`…), never a default; the app's String Catalog and its `l10n.py` workflow are untouched. The look (the
  app's buttons, chips, titles, cards) comes in as styles or views; only chrome that is the same in every app lives
  here.
- **No permission prompts, no network.** System signals read device and window state. If an API would prompt the user
  or touch the network, it doesn't belong here.
- **Behavior changes are additive and vector-tested.** New scheduler behavior gets a config field whose default keeps
  every existing vector byte-identical, a unit test, a vector case, and the C# port in the same change
  (`dotnet test dotnet/Laching.Tests.slnf` tells you what to port). New fields in a companion vector are optional and omitted at
  their default, so the apps' committed vectors stay byte-identical; the C# loaders read an absent field as the default.
- **`conformance/companion` pins mechanism, over fixtures.** The picker, cadence, quiet rules, coordinator, desktop
  presence and rig are pinned here over `LachingTesting`'s fixture bank, defaults and skins; each app pins its own
  content (bank, defaults, picks, poses) with the same generators and replays.
- **Rasters never shear.** A character's transforms are rotation, translation and uniform scale only, so an image part
  draws under the same `affine` as a vector one. Pose vectors pin each layer's name, the six affine numbers and the
  content kind, never pixels; the menu bar glyph stays a vector line drawing for every skin.
- **C# mirrors Swift** folder for folder and file for file (`Sources/LachingCore/Scheduling/ReminderScheduler.swift` ↔
  `dotnet/Laching.Core/Scheduling/ReminderScheduler.cs`). Core folders: `Scheduling/`, `Random/`, `Localization/`, `Settings/` (`Drawing/` is Swift and Kotlin only, with no C# twin); Companion folders:
  `Phrases/`, `Cadence/`, `Rules/`, `Settings/`, `Coordination/`, `Desktop/`, `Figures/`, `Skins/`. `LachingSystem`
  and `LachingShell` map onto `Laching.Windows/System/` and `Laching.Windows/Shell/`. Where the C# side deviates, the
  row in `docs/layout.md` says so: the store lives under `dotnet/Laching.Core/Store/`, `Laching.Testing` has a layout
  of its own, and the companion shell (`Laching.Companion.Shell`, `Laching.Windows.Companion`) is mirrored by role, not
  file for file, because the Swift files mix logic and AppKit, so the C# side splits them, keeping the type and member
  names (`plans/0.8/README.md` maps every Swift file). Tests mirror the same folders, with `Conformance/` in each Core
  and Companion test project.
  No reflection-based JSON (source-generated contexts only): the apps publish with Native AOT and warnings are errors.
- **A programmer error stops each port in its own idiom.** A misuse the type system can't catch (a `SkinCatalog` whose
  default skin isn't free, a joke with one part) is a `precondition` in Swift and an `ArgumentException` in C#
  (`ArgumentOutOfRangeException` for a number out of range), with the same message text: interpolate raw values
  (`skin.rawValue`, `mood.rawValue`; `RawValue()` in C#), never a Swift value's default description. Swift can't test a
  trap, so each C# throw gets a test that pins it. C# also throws `ArgumentOutOfRangeException` from a switch's default
  arm for an enum value outside the known cases, where Swift's exhaustive switch needs nothing; those get a test too. A
  value built with a hole in it (`RiggedCharacter`'s art missing a part its rig names) is a `preconditionFailure`
  against an `InvalidOperationException`. A member a subclass must override is a `fatalError` in Swift and `abstract` in
  C#. Kotlin, which ports Core and the testing helpers, follows the same rule with `require` (an
  `IllegalArgumentException`). A mistake in a file an app ships (`phrases.json`, character art) isn't a programmer
  error: the loaders throw, on every side.
- **Strings:** the package ships none. Titles for notification actions and the like are passed in already localized.
- **Versioning:** SemVer, one `vX.Y.Z` tag per release for every side. There is no changelog file: the release notes
  are GitHub's, from the merged pull requests' titles, so a pull request's title says what changed for an app, and a
  breaking one is labelled `breaking` (`docs/issues.md`). A change never bumps the version or tags: releases are made
  on demand by dispatching `release.yml`, which numbers the release from those labels (a patch, or a minor while 0.x
  when one is `breaking`) with `scripts/release.sh plan`, and its `bump` puts the number in the hand-kept strings:
  `Laching.version` (`Sources/LachingCore/Laching.swift`), `lachingVersion` (`kotlin/gradle.properties`),
  `site/index.html` (the `vX.Y.Z` label in the header and both `exact:` install snippets) and `README.md` (the `exact:`
  install pin and the latest release under *Releases*). `LachingInfo.Version` and Kotlin's `Laching.VERSION` are
  generated at build time. One tag per release: Swift and Kotlin take the tag; NuGet and npm get a new version only
  when their code changed since their last one. The six NuGet packages share `LachingVersion`
  (`dotnet/Directory.Build.props`), which `bump` moves only then. `scripts/verify.sh` fails when the strings disagree
  or `LachingVersion` is above the tag. The `release` skill has the details.
  The six `Laching.Web.*` packages work the same way on their own `LachingWebVersion` (`web/Directory.Build.props`),
  shipped when `web/src` or `web/`'s build files changed since `v<LachingWebVersion>` (`scripts/release.sh web`).
- **The web packages (`web/`)** follow the same rules where they apply (doc comments on public types, no app's name or
  strings, secrets from configuration only) and add their own:
  - The jobs table's SQL names (table, columns, indexes) never change: they are in apps' migration history. A new
    column is additive, with a default.
  - The Tailwind CLI's version and its SHA-256 list change together, in `build/Laching.Web.Tailwind.targets`; an
    upgrade is a release.
  - When `web.yml`'s Monday check says Cloudflare's IPv4 ranges changed, update
    `src/Laching.Web.Aspire/cloudflare-ips-v4.txt` and release.
  - Tests never reach the network; the Jobs tests need PostgreSQL and each makes its own database.
- Formatting is enforced in CI: `.swift-format` (4 spaces, 140 columns), `dotnet/.editorconfig`, `web/.editorconfig`
  and `kotlin/.editorconfig` (ktlint's `ktlint_official` style, 120 columns; `kotlin.yml` pins its version).
