# Laching tools

Python tools (standard library only, unless noted), shell scripts and agent skills for companion apps built on
Laching: localization, phrase sync, recorded voices, character art, sounds, fonts, screenshots, verification and the
agent hook. Every tool reads the app's `companion.json` at its root (found by walking up from the current directory,
or `$COMPANION_ROOT`); the schema is [`companion.schema.json`](companion.schema.json). No tool names an app.

| Tool | What |
|---|---|
| [`companion.py`](#companionpy) | Loads and validates `companion.json`; the Laching pins, moved, linked and checked. |
| [`conform.py`](#staying-on-lachings-tools) | `companion.py conform` and `apps`: the app stays on these tools, not copies. |
| [`bump-apps.sh`](#bump-appssh) | `companion.py bump` in every app checked out next to this Laching checkout. |
| [`l10n.py`](#l10npy) | String Catalog gaps, work files for translators, `.resw`/`.po` exports. |
| [`phrases.py`](#phrasespy) | Keeps the catalog's `phrase.<id>` keys in step with `phrases.json`. |
| [`voice.py`, `voice-review.html`](#voicepy-and-voice-reviewhtml) | Records, reviews and publishes the character's voices with ElevenLabs. |
| [`character.py`](#characterpy) | Compiles the app's skins to the JSON and Swift both cores draw from. |
| [`synth.py`](#synthpy) | The sound engine. |
| [`fonts.py`](#fontspy) | Builds the display faces from pinned variable fonts. |
| [`screenshots.sh`](#screenshotssh) | Window captures in light and dark. |
| [`store-screenshots.py`](#store-screenshotspy) | The App Store's, Google Play's and the Microsoft Store's images. |
| [`check-layout.sh`](#check-layoutsh) | `screenshots.sh` in every language, naming the languages whose windows don't fit. |
| [`check-layout-android.py`](#check-layout-androidpy-and-avdpy) | The Android app's screens in every language, on emulators. |
| [`avd.py`](#check-layout-androidpy-and-avdpy) | Boots each AVD once, headless, from a snapshot. |
| [`check-layout-ios.py`](#check-layout-iospy-simpy-and-layoutcheckpy) | The iPhone and iPad app's screens in every language, on cloned Simulators. |
| [`sim.py`](#check-layout-iospy-simpy-and-layoutcheckpy) | Builds the app for the Simulator, boots, clones and launches it. |
| [`layoutcheck.py`](#check-layout-iospy-simpy-and-layoutcheckpy) | The report both device checkers end with. |
| [`screens.py`](#screenspy) | The same screens on the Mac, Windows, iOS and Android, from one manifest. |
| [`install-dev.sh`, `dev-icon.swift`](#install-devsh-and-dev-iconswift) | The Debug build in `~/Applications`, and its DEV-banded icon. |
| [`conformance.sh`](#conformancesh) | Regenerates the app's vectors from its Swift core. |
| [`verify.sh`](#verifysh) | Runs the checks that apply to changed paths, plus the pin check. |
| [`verify-lib.sh`](#verify-libsh-and-verify-lockpy) | The shared harness the app's own checks register with. |
| [`verify-lock.py`](#verify-libsh-and-verify-lockpy) | The named build locks across verifier sessions. |
| [`ios-destination.py`](#ios-destinationpy) | The `-destination` for the iOS tests. |
| [`setup-linux.sh`](#setup-linuxsh) | Prepares a Linux container (cloud agents). |
| [`store/check.py`](#storecheckpy) | Each field of the store listing against the stores' limits. |
| [`store/push.py`](#storepushpy) | The store listing to App Store Connect. |
| [`store/import.py`](#storeimportpy) | A Partner Center import folder, in parts. |
| [`store/push-microsoft.py`](#storepush-microsoftpy) | The Microsoft Store through the Partner Center submission API. |
| [`store/push-google.py`](#storepush-googlepy) | Google Play through the Play Developer API. |
| [`landing/`](#landing) | `@laching/landing`, a landing page's plumbing as Vite plugins, on npm. |
| [`hooks/hook.py`](#hookshookpy) | Agent guardrails. |
| [`skills/`](#skills) | The generic agent skills an app links next to its own. |
| [`ci-macos.py`, `macos-app.yml`](#mac-apps) | Reusable Mac CI. |
| [`ci-windows-core.py`, `windows-core.yml`](#portable-windows-checks) | Portable Windows CI. |
| [`companion_ci.py`](#portable-windows-checks) | What both CI drivers share. |
| [`windows/companion.targets`](#windows-apps) | What every companion's WinUI project imports, last. |
| [`windows/screenshots.ps1`](#windowsscreenshotsps1) | The WinUI app's windows in light and dark. |
| [`windows/check-layout.ps1`](#windowscheck-layoutps1) | `screenshots.ps1` in every language, naming the languages whose text doesn't fit. |
| [`windows/store-release.ps1`](#windowsstore-releaseps1) | The Microsoft Store release in one command. |
| [`windows/smoke-package.ps1`](#windowssmoke-packageps1) | Installs a built `.msix` the way a customer gets it and checks it opens. |
| [`windows/check-winui.py`](#windowscheck-winuipy) | WinUI code that runs in Debug but breaks the Native AOT build or the XAML compiler. |
| [`windows/assets.py`](#windowsassetspy) | The MSIX logos and `AppIcon.ico` from the app icon. |
| [`windows/vm.sh`](#windowsvmsh) | On a Mac, the WinUI app built and checked in a Parallels Windows VM. |
| [`windows-release.yml`, `windows-app.yml`](#windows-releaseyml-and-windows-appyml) | Reusable workflows: the Store release and the WinUI build check. |

Tests: `python3 tools/tests/run.py` (over `tools/tests/fixture-app`, a minimal app, the files side by side, `--jobs N`
at once; `tools.yml` runs them; Python 3.10 or later, which an older `python3` finds and re-runs under), and for the
landing package `npx vp test run` in `tools/landing`. `scripts/verify.sh --fast` runs only the tests that cover the
changed files, from the map in `tools/tests/covering.py`: a new tool or test file goes in it (`test_covering` says so).

An app without a phrase bank or a character (a reminder app, say) leaves `character` and `conformance` out of
`companion.json`: `l10n.py`, the store tools, `verify.sh` and the hook work as usual; `phrases.py`, `voice.py`,
`character.py` and `conformance.sh` say the app has no conformance section.

## Xcode builds share one compilation cache

Each xcodebuild the tools run passes `companion.xcode_cache_settings()`.
`screenshots.sh`, `sim.py`, `l10n.py` and `verify.sh`'s test get them from `companion.py env` as `$XCODE_CACHE`. The
settings turn on Xcode's compilation caching (`COMPILATION_CACHE_ENABLE_CACHING`, Xcode 26 and later). They also point
it at the cache Xcode keeps when a build names no derived-data folder: `~/Library/Developer/Xcode/DerivedData/CompilationCache.noindex`.
Without that, a build given `-derivedDataPath` keeps its cache inside that folder. A new worktree's first build of
`build/screenshots.noindex` then compiles everything again.

The builds that are only run, `screenshots.sh` and `sim.py` (`$XCODE_CACHE_MAPPED`), also map their paths
(`SWIFT_`/`CLANG_ENABLE_[PROJECT_]PREFIX_MAPPING`). The SDK, project and derived-data folders then leave the cache keys,
so a checkout at another path reuses the compiles too. On mybackhurts, a cold build into a second folder took:

| Build | No cache | Shared cache | Shared cache, mapped |
|---|---|---|---|
| Mac Debug | 40 s | 19 s | 8 s |
| iOS Simulator Debug | 33 s | 24 s | 9 s |

Mapping also writes those paths as `/^src`, `/^sdk`… into the debug info, `#filePath` and `.stringsdata`. `l10n.py`
reads strings from a build, and tests use `#filePath`, so both get the cache without the mapping.
`LACHING_COMPILATION_CACHE=0` turns the cache off for every tool. The cache holds about 0.4 GB per app for the Mac and
0.6 GB for the Simulator, and it's safe to delete.

## Adopting the tools in an app

The steps, from the submodule to an Android app's Gradle build: [`docs/getting-started.md`](../docs/getting-started.md).

## `companion.py`

Loads and validates `companion.json`; `get`, `path`, `root`, `validate`, `env` (paths and path patterns for shell),
`pins` (the Laching version everywhere it's pinned), `bump` (every pin to a release), `link`/`unlink` (build against a
Laching checkout): below, [*Moving to a release, or trying one first*](#moving-to-a-release-or-trying-one-first);
`conform` and `apps` (`conform.py`): below, [*Staying on Laching's tools*](#staying-on-lachings-tools); `checks` (the
app's own `verify.checks`, for `verify.sh`).

### Moving to a release, or trying one first

**`bump`** moves an app to a release in one go: every pin `pins` lists (`laching.tag`, the core package's `exact:`, the
XcodeGen spec's `exactVersion`, each `Laching.*` NuGet version, to the release's `LachingVersion`; the Kotlin build's `lachingVersion` is the submodule's own, so it follows the submodule), the
committed `Package.resolved` files, the submodule
(checked out at the tag) and the landing page's `@laching/landing`; it regenerates the Xcode project and prints `pins`.
It refuses a tag origin doesn't have, and changes nothing when the app is already there.

```sh
python3 laching/tools/companion.py bump 0.9.1   # in an app
tools/bump-apps.sh 0.9.1                         # in the Laching checkout: every app next to it, then each verify.sh --fast
```

- `@laching/landing` ships only when `tools/landing` changed, so the landing page gets npm's newest version at or below
  the release. Its lockfile follows through the landing
  tool's `install` (`landing.tool`), unless the tool is missing or the landing page has uncommitted dependency changes,
  which `bump` then says.
- The core package's `Package.resolved` is rewritten in place (its `originHash` is the SHA-256 of its manifest). The Xcode
  project's goes through `xcodebuild -resolvePackageDependencies`, because its `originHash` covers the checkout's path;
  without Xcode, `bump` says what's left.
- Nothing is staged or committed: commit the app's paths, and the landing page's change inside its own repository.

**`link`** builds an app against a Laching checkout instead of the release, so a change that spans Laching and the
apps can be tried in each of them before one tag covers the batch:

```sh
python3 laching/tools/companion.py link     # ../laching, next to the app (or: link PATH)
# build and test as usual: xcodebuild, swift test, dotnet build and test
python3 laching/tools/companion.py unlink   # before committing
```

- **Swift:** the XcodeGen spec's and the core package's Laching become `path:` packages, and the project is
  regenerated. SwiftPM names a local package after its folder, so keep the checkout's folder named `laching`.
- **.NET:** a `Directory.Build.targets` in the Windows folder (created, or added to before its `</Project>`) swaps each
  `Laching.*` PackageReference for a ProjectReference to the checkout's `dotnet/<Package>/<Package>.csproj`: no feed,
  no version, nothing to pack, and every build compiles the checkout as it is. The WinUI packages build on Windows
  only, as before.
- **Kotlin:** `link` writes `laching.kotlin.dir=<checkout>/kotlin` into the Android folder's git-ignored
  `local.properties` (the line the `settings.gradle.kts` in [`docs/getting-started.md`](../docs/getting-started.md)
  reads), so the included build is the checkout; `unlink` takes the line out again.
- **While linked, `pins` fails**, and with it `verify.sh` and the pre-commit hook, so a linked app can't be committed;
  `bump` refuses too.
- **`unlink`** puts back every file `link` changed, byte for byte, and the `Package.resolved` files and the project the
  linked builds rewrote, then regenerates the project. A file edited meanwhile keeps that edit: only the link's own
  change comes out. The record of the link is in the repository's git folder (`.git/laching-link`, one per worktree).
- The tools stay the submodule's: `link` switches the packages, not `laching/tools`.

### Staying on Laching's tools

`verify.sh` runs `companion.py conform` on every run, so an app's `verify.sh`, pre-commit hook and the agents' Stop hook
fail when the app drifts from the tools (each rule: `conform.py`'s docstring):

- **No copies.** A file named like one of the tools (by name without its extension: `smoke-package.ps1`,
  `check-winui.py`, `push.py` and `import.py` under the store listing, `store-screenshots.*`, `verify.sh`,
  `setup-linux.sh`, ..., and `store-packages.ps1`, which `store-release.ps1` replaced) is a thin wrapper: at most 12 lines
  of code that name `laching/tools/`. One that is the app's own on purpose (a conformance script writing the app's own
  vectors) goes in `conform.allow`, with why.
- **The reusable workflows**, at the release the app pins: an app with `windows.app` calls both
  `windows-release.yml` and `windows-app.yml`, and no workflow of its own publishes, smoke-tests or bundles Store
  packages. Every call of a Laching reusable workflow, including `macos-app.yml`, must name the pinned release.
- **Linked skills.** A skill in `.agents/skills` named like one of `tools/skills` is a link to
  `../../laching/tools/skills/<name>`; the app's own words for it go in `.agents/notes/<name>.md`.
- **`store.microsoft`** is the schema's shape: `shots` and `publisher`, and `storeId` for the submission API.

`companion.py apps` (or `bump-apps.sh --report`), in the Laching checkout, lists the apps next to it:
the release each pins, how far behind the newest, and its conform problems; it exits 1 while they pin different
releases.

## `bump-apps.sh`

`companion.py bump` in every app checked out next to this Laching checkout (or the ones named), then each one's
`verify.sh --fast`; commits nothing. `--report` prints `companion.py apps`: each app's pin against the releases, and
whether it conforms. `bump-apps.sh` runs the Laching checkout's `companion.py`, so it moves an app whose submodule
predates `bump` (the example: [*Moving to a release, or trying one first*](#moving-to-a-release-or-trying-one-first)).

## `l10n.py`

String Catalog gaps, work files for translators, apply with validation, `.resw`/`.po` exports; language-scoped phrases.

## `phrases.py`

Keeps the catalog's `phrase.<id>` (and `.n` for jokes) keys in step with `phrases.json`; `check` also reports a phrase
whose weight is under 1.

## `voice.py` and `voice-review.html`

Records, reviews and publishes the character's voices with ElevenLabs; a clip per joke part.

## `character.py`

Compiles the app's skins (vector or raster; `characters` in `companion.json`) to the JSON and Swift both cores draw
from; `--check`.

## `synth.py`

The sound engine; the app's recipes module defines `sounds(engine)`; `--check`.

## `fonts.py`

Builds the display faces from pinned variable fonts (`fonts`, plus `fonts.faces` for an upright beside an italic; needs
fontTools).

## `screenshots.sh`

Window captures in light and dark (failing on the problems `ScreenshotCapture` reports: content taller than its window,
a shot not drawn or written; `--only` runs hidden, so the Mac stays usable, with `--front` for key windows; `--skin id`
wears that skin, owning in the sample store what unlocks it; `--themes dark` shoots one appearance; each window opens
once and is shot as soon as it has drawn, in light and then in dark).

## `store-screenshots.py`

- App Store images (`--lang LANG|all`: another language's windows are captured in it first; `--only shot,…` re-renders
  those images alone and keeps the folder's others, refusing a folder that lacks them).
- The App Store's iPhone, iPad and iPhone Duo images (`--canvas iphone|ipad|duo`: the caption above the simulator's
  screen, from build/store-captures/ios/<device>/<lang>, into push.py's folders).
- Google Play's from the Android captures (`--canvas android-phone` 1080 × 1920 with the caption above the screen,
  `android-tablet` 2560 × 1600; Play's size limits checked).
- The Microsoft Store's (`--canvas microsoft`: `store.microsoft.shots`, the Windows captures in upload order, every other
  language's captured in one screenshot run, each centered unscaled on a 3840 × 2160 backdrop, or with
  `store.microsoft.captioned` laid out as the Mac's with its caption beside the window; refuses a capture larger than
  the canvas or one flat color, as before the window drew).

`screenshots.store.style` `zine` offsets the headline in the accent and frames the window in ink, `cjkFonts` sets a
language in its own face, with Japanese and Chinese broken between characters, and a caption's `ios`, `windows` or
`android` object replaces its fields on that platform's canvases (needs Pillow).

## `check-layout.sh`

`screenshots.sh` in every language (or the ones named), side by side and hidden, naming the languages whose windows
don't fit.

## `check-layout-android.py` and `avd.py`

The Android app's screens in light and dark (`ScreenshotRun` in `laching.android`), in every language (or the ones
named) on the AVDs of the `screens.platforms` entries captured on android (or `--devices`): each AVD boots once,
headless (`avd.py`: `emulator -no-window`, the status bar in demo mode at 09:41), from a snapshot: the first run on an
AVD cold-boots it, sets demo mode and saves the snapshot `laching-screens` (`laching-screens-camera` for a run with the
webcam, since a snapshot holds the camera setup), and later runs load it with `-no-snapshot-save`, so each starts from
the same memory and disk in seconds instead of a cold boot (`<avd>.avd/laching-screens.json` beside it records the
emulator's and the system image's revisions; a new emulator or image, a snapshot that doesn't load or `--cold`
cold-boots and saves a new one), installs the Debug APK (built once, or `--apk`) and runs every language on it, one
launch per distinct shot env in screenshot mode with Laching's intent extras (`LACHING_SCREENSHOT_MODE`,
`LACHING_SCREENS`, `LACHING_LANG`, `LACHING_REGION`), naming the AVD and language of each screen that doesn't fit;
`--jobs` emulators at a time (`LAYOUT_JOBS`), `--font-scale 2.0` for the largest text, `--posture closed|half-open|open`
to fold a foldable AVD and `--orientation portrait|landscape` to turn the screen (else the platform's `posture` and
`orientation`).

## `check-layout-ios.py`, `sim.py` and `layoutcheck.py`

The iPhone and iPad app's screens in light and dark (`ScreenshotCapture` on iOS), in every language (or the ones named)
on iPhone SE, the largest iPhone, iPad Pro 13" upright, turned and in a Split View third, and the iPhone Duo closed: one
launch per language on cloned Simulators side by side (`--jobs`, `LAYOUT_JOBS`; each clone boots once, its template
made with accessibility on), naming the device and language of each screen that doesn't fit (content taller than the
screen, text cut short or pushed off it). `--text-size` for Dynamic Type.

`sim.py` (shared with `screens.py`) builds the app for the Simulator into one `build/ios-simulator.noindex` both tools
use, boots it (a second time only when its defaults weren't already written), clones it (`on_clones`; a killed run's
clones go at the next run) and launches it with `-AppleLanguages (<lang>)` and `-AppleLocale <region>` (en_US,
pt_BR…), logging each command with the seconds it took, as `avd.py` does; `layoutcheck.py` is the report both device
checkers end with.

## `screens.py`

The same screens on the Mac, Windows, iOS and Android in any languages, from one manifest (`screens` in
`companion.json`: each canonical screen's shot on each platform): `capture` builds each platform once and runs them
side by side (the Mac hidden, its languages side by side; Windows in the Parallels VM through `vm.sh check-layout`;
iOS on simulators of its own on the newest runtime that runs each device, each booted once, each language one launch of
the app in screenshot mode, as `check-layout-ios.py` runs it, side by side on clones when a device gets more than one
simulator; Android on a headless emulator per platform (its `avd`), as `check-layout-android.py` runs it, from the
AVD's snapshot (`--cold` boots cold and saves a new one), a shot's `camera` giving the emulator the host's webcam, the
platform's `posture` folding a foldable and its `orientation` turning the screen; `--jobs` is the app processes,
simulators and emulators at once in all, shared among the platforms in proportion to their runs, at least one each;
`--themes` reaches every launch, so a capture of one theme never draws the other) into
`build/screens/<platform>/<lang>/<screen>-<theme>.png`, with every platform's problems in one `problems.txt`; `compare`
makes one sheet per screen, language and theme (desktops on top, devices below, one height per row) and an
`index.html` to flip through them; `diff --against NAME` and `accept NAME` keep a perceptual hash per image, so a
review looks only at what changed. Needs Pillow (re-runs under `/usr/bin/python3` when this Python lacks it).

## `install-dev.sh` and `dev-icon.swift`

The Debug build in `~/Applications/<devAppName>.app`; its DEV-banded icon.

## `conformance.sh`

Regenerates the app's vectors from its Swift core.

## `verify.sh`

Runs the checks that apply to changed paths, plus the pin check.

What `verify.sh` runs for a path (`--help` lists them): documentation (`.md`, `.markdown`, `.rst`, `.adoc`) leaves the
app checks alone, so only the store listing's check and the app's own checks marked `docs` see it, unless `--all`. The app's Swift is
`mac.app`, `mac.tests` and `mac.format`'s folders outside the core package; with `mac.iosScheme`, `xcodebuild test`
runs the iOS tests too, on `ios-destination.py --own "<name> verify"`. `--staged`, the pre-commit hook's mode,
generates the Xcode project from the index in a scratch copy, so another session's untracked sources don't count.
The WinUI app builds when the app, the Windows core it references, `Directory.Build.props`,
`Directory.Packages.props` or `global.json` changes; `--no-vm` (or `LACHING_NO_VM=1`) leaves the Parallels VM to a session that holds it. On a Mac, Gradle gets Homebrew's
openjdk@17 and Android Studio's SDK unless `JAVA_HOME` and `ANDROID_HOME` are set. `verify.sh` runs
`character.py --check` and `synth.py --check` when their inputs, their outputs or the `laching/` pin change.

The app's own checks, which a copied `verify.sh` used to hold, are `verify.checks` in `companion.json`: each a `name`, the
`paths` that trigger it (an extended regular expression), the command to `run` (bash, from the root), `slow` for one
`--fast` skips and `docs` for one that documentation changes trigger too (a skill folder's links, say).

## `verify-lib.sh` and `verify-lock.py`

The shared harness owns execution, progress, timeouts, process cleanup, named build locks and changed-path discovery;
apps register their own checks.

The shared `verify-lib.sh` also supports app-owned check selection: source it from the repository root, call
`discover_changes "$root" "$landing"` after parsing `mode`, `base` and `paths`, then register `check`, `skip`,
`summary` and `finish` calls. `find_tool <name> <package-dir>` resolves a global CLI or the package's
`node_modules/.bin` installation. Submodule discovery removes the parent Git hook's repository variables.

Each check runs in its own process group. Progress appears after ten seconds and every five minutes; after
`LACHING_CHECK_SECONDS` (default 1800), the check fails and its process group is stopped. Interrupting the verifier
also stops its active group. `LACHING_PROGRESS_SECONDS` and `LACHING_PROGRESS_INTERVAL` change progress intervals;
`LACHING_VERIFY_TIMING=1` adds elapsed times. `check "name" locked <build-name> <command...>` serializes a build
across verifier sessions in this checkout. Locks live in `build/verify-locks` (or `LACHING_VERIFY_LOCK_DIR`);
`verify-lock.py` holds an OS advisory lock in a process group separate from the build. The verifier stops the build
and its descendants before releasing the lock, including on timeout or interruption. OS locks also release if their
holder crashes. The persistent
lock files are diagnostic and can contain an old PID; stale contents never prevent acquisition. Keep these files in
place while builds are running, so every process locks the same file.

## `ios-destination.py`

The `-destination` for the iOS tests: an iPhone on the newest iOS this Xcode has, or with `--own NAME` a simulator of
that name (the same model, made on first use) that the Simulator panel and the screenshot runs never boot.

## `setup-linux.sh`

Prepares a Linux container (cloud agents): submodules, .NET, landing dependencies, and for an app with an `android`
section a JDK 17 and ktlint.

## The store listing: `store/`

The store listing (`store` in `companion.json`): one check against every store's limits, then a tool per way it reaches
a store (two for the Microsoft Store: the Partner Center import and the submission API).

### `store/check.py`

Each field against the stores' limits (with `store.apple.ios`, the iPhone and iPad version page's too; with
`store.google`, Google Play's `google.*` fields: title 30, short description 80, full description 4000, each product's
name 55 and description 200; Google Play's push is `push-google.py`).

### `store/push.py`

App Store Connect (text, in-app purchases, screenshots, previews, review notes, and a universal app's iOS page and
iPhone and iPad screenshots; `--status` reads only).

### `store/import.py`

A Partner Center import folder, always in parts of at most 20 MB and 6 languages (`partN/`, imported one after another:
one big import fails).

### `store/push-microsoft.py`

The Microsoft Store through the Partner Center submission API (`push-microsoft.py status|prepare|commit`: the bundle,
the listing text, screenshots and art into a new submission from the last published one, dry run without `--apply`;
`store.microsoft.storeId`; the credentials of one Entra application for every app of the account, set up as
`store/microsoft-api-setup.md` says).

### `store/push-google.py`

Google Play through the Play Developer API (`push-google.py status|prepare`: the listing text in every language, the
icon, feature graphic and phone and tablet screenshots in one edit, and `store.apple.iaps` as one-time products priced
in USD and converted per region; dry run without `--apply`; `store.google.packageName`; a service account's JSON key,
set up as `store/google-api-setup.md` says).

## `landing/`

`@laching/landing`, an npm package (Node 24): a landing page's localized pages, legal pages, sitemap, `_headers` and
social cards as Vite plugins. Published to npm by `release.yml`; the app's landing repository depends on it by version.

## `hooks/hook.py`

Agent guardrails: generated files, the stop-time checks (ktlint over the Kotlin files an agent edits), the Windows and
Android parity reminders. Wiring it into Claude Code, Codex and Copilot: [`docs/getting-started.md`](../docs/getting-started.md),
step 4.

The hook reads the guards, the slow-check table and the parity patterns (`hooks.*`) from `companion.json`, and
fails open when the file is missing or invalid. It guards what Laching's generators write without being told, each
refusal naming the command that rewrites the file: `character.py`'s character vectors, `<Skin>Art.swift` files and,
for raster skins, its asset folders (from `characters`); `synth.py`'s `.wav` files (from `sounds`); and
`dev-icon.swift`'s `AppIcon-Dev.appiconset` PNGs under `mac.dir`. A file only the app's own script makes (its
`AppIcon.appiconset` from its icon renderer) is a `hooks.guards` entry. The hook also refuses to load any text file
over `hooks.bigFileLines` lines (600) whole: a `Read` without `offset`/`limit`, `cat`, or `sed` without a range; the
refusal names the outline command (`grep -n '^\(func\|class\|...\)'`) to run first. On `Stop` it tells the user, once
per compaction cycle, when Claude's context is past `hooks.longSessionTokens` (130000; 0 disables), so the next task
goes to a fresh session. On `SessionStart` after a compaction or a resume (`hook.py resume`) it prints where the session
was, as the agent's context: the files it edited, the slow checks it ran, the branch, git status and the recent
commits (nothing when the tree is clean and the session has no log).

## `skills/`

`localization`, `add-phrase`, `add-setting`, `conformance`, `voice`, `windows-port`, `release-mac`, `release-ios` (a
universal app's iPhone and iPad version), `release-windows`, `review-invariants`.

They read the app's prose from its `DESIGN.md` (the character's voice, the glossary, the palette) and from
`.agents/notes/<skill>.md` when present (voices chosen, the team ID, who reviews which language);
`review-invariants` adds the app's `.agents/review-appendix.md` to its checklist. Linking them into an app:
[`docs/getting-started.md`](../docs/getting-started.md), step 5.

## Mac apps

Reusable Mac CI (`ci-macos.py`, `../.github/workflows/macos-app.yml`): project generation, formatting, release pins,
enabled content checks, core tests and app tests from `companion.json`.

An app can keep its `workflow_dispatch`, permissions and concurrency policy in a short workflow and call the shared
Mac checks at the release it pins:

```yaml
jobs:
  test:
    uses: vmihalachi/laching/.github/workflows/macos-app.yml@vX.Y.Z
    with:
      hosted: ${{ inputs.hosted }}
      submodules: "true"  # also checks out bundled recorded voices
```

The self-hosted runner defaults to `["self-hosted","macOS"]`; `runner` accepts another JSON label list. `hosted: true`
uses `macos-26` and selects the latest stable Xcode. `laching-path` defaults to `laching` and must match the profile's
`laching.path`. Both `companion.py bump` and `pins` include this workflow reference; `conform` reports a drifting ref.

`ci-macos.py` runs named stages (`project`, `format`, `pins`, `content`, `core`, `test`). Paths and the test scheme come
from `mac`; content checks follow the configured sounds, characters and voice, plus the phrase bank when present.
An app without a core package skips that stage. Mac CI requires `mac.format`: an explicit, nonempty array of every
owned Swift source directory or `.swift` file, relative to `mac.dir`, including shared/platform sources and core
sources/tests. For example, `["Desktop", "Mobile", "Common", "Tests", "Core/Sources", "Core/Tests", "Core/Package.swift"]`.
Missing roots fail the formatting stage; the helper makes no assumptions about folder names.
Every command is an argument list, so app names and paths with spaces retain their meaning; the workflow accepts no
shell command input. Output streams to the job and `mac.dir/build/ci-<stage>.log`; failure uploads these logs. Apps with
other platform checks keep those in their own workflows.

## Portable Windows checks

Portable Windows CI (`ci-windows-core.py`, `../.github/workflows/windows-core.yml`): pins, strings and phrase keys,
generated `.resw`, C# formatting, WinUI lint, core tests and optional app model tests.

An app's `windows-core.yml` keeps its dispatch, concurrency and runner policy and calls
`vmihalachi/laching/.github/workflows/windows-core.yml@vX.Y.Z`. Its inputs match the Mac workflow: `hosted: true`
selects `ubuntu-latest`; `runner` supplies the self-hosted JSON labels; `laching-path` must match the profile;
`submodules: "false"` checks out only Laching explicitly, while `"true"` also initializes recorded voices for app tests.
The self-hosted machine supplies .NET 10 and Python; hosted runs install them.

`ci-windows-core.py` runs `pins`, `content`, `strings`, `format`, `lint`, `core` and `app`. Paths come from `windows.dir`,
`windows.strings`, `windows.coreTests` and optional `windows.appTests`; test paths can name a project or its directory.
Content checks include localization and an existing phrase bank. Generated `.resw` files are exported to a fresh
scratch directory and compared as a complete tree, allowing CRLF on Windows. An app with `windows.app` gets WinUI
lint; without `windows.appTests`, the model-test stage is empty. The app's catalog checks also work when its landing
submodule is uninitialized. Logs go to `mac.dir/build/ci-windows-core-<stage>.log` and are uploaded on failure.

Both CI drivers share command execution, release-pin commands and the pinned-tool-path guard in `companion_ci.py`.
All subprocesses use argument lists; neither workflow accepts shell command inputs. `companion.py bump`, `pins` and
`conform` cover both reusable workflow refs.

## Windows apps

A companion's WinUI 3 app (`windows.app`, on `Laching.Windows.Companion`) takes its build from the submodule, so every
app builds, packages and checks the same way. Its `.csproj` sets its own properties, then imports the targets
(`windows/companion.targets`) last:

```xml
<PropertyGroup>
  <TargetFramework>net10.0-windows10.0.22000.0</TargetFramework>  <!-- Windows 11, as the Laching.Windows packages -->
  <TargetPlatformMinVersion>10.0.22000.0</TargetPlatformMinVersion>  <!-- the MSIX's MinVersion -->
  <AssemblyName>MyApp</AssemblyName>
  <CompanionVoiceFolder>..\..\voices\Voice</CompanionVoiceFolder>           <!-- → Voice\<lang>\<voice>\<id>.m4a -->
  <CompanionSoundsFolder>..\..\macos\MyApp\Resources\Sounds</CompanionSoundsFolder>  <!-- → Sounds\*.wav -->
  <CompanionFontsFolder>..\..\macos\MyApp\Resources\Fonts</CompanionFontsFolder>      <!-- → Fonts\*.ttf -->
</PropertyGroup>
<Import Project="..\..\laching\tools\windows\companion.targets" />
```

- **Debug is unpackaged** (`dotnet run --project windows/MyApp.App -p:Platform=x64`): no Developer Mode, no
  registration, the screenshot runner as is. Without package identity `LocalSettingsStore.ForApp` keeps the settings
  in a JSON file and the app takes `MockStoreBackend` (`PackageIdentity.IsPackaged`). **Every other configuration is an
  MSIX with Native AOT**; `-p:WindowsPackageType=None` builds a Release exe that runs unpackaged, which is what
  `screenshots.ps1 -Configuration Release` shoots.
- **Notifications need the MSIX.** Unpackaged, Windows 11 may never show the app's notifications, although it logs them
  as delivered and Settings says they're on, and after Quit a button on one starts nothing (COM finds no server). So check
  notifications, their buttons and the startup task in the installed package (`smoke-package.ps1 -Keep`), never in a
  Debug run.
- **Native AOT breaks what a Debug run hides**: a cast of an untyped XAML object throws or quietly fails, so the app
  goes through `XamlCast.As<T>`; `check-winui.py` fails on the patterns, and `verify.sh` runs it on the changed files.
  The Release screenshots and `smoke-package.ps1` on the built package are the proof before an upload.
- **The model tests run on any OS.** The app's model, its words and the window list use no WinUI type; what has a
  WinUI half sits under `#if WINDOWS` (defined for `-windows` target frameworks only). A `net10.0` test project
  (`windows.appTests`) compiles those files by link and reads the app's English `.resw` through `ReswStrings`, so its
  assertions read the real words; `verify.sh` runs it when the app or it changes.
- **Wrappers**, as for the shell scripts ([*Adopt the tools*](../docs/getting-started.md#adopt-the-tools), step 3):
  `windows/scripts/screenshots.ps1` and `smoke-package.ps1`, e.g.

  ```powershell
  & "$PSScriptRoot/../../laching/tools/windows/screenshots.ps1" @args
  exit $LASTEXITCODE
  ```

- **The traps** the kit and the lint know about: `App.xaml` sits next to the `.csproj` (elsewhere it isn't the
  application's XAML, and the app stops at the first custom type with a message-less `XamlParseException`); no element
  named `Language`; no `x:Bind` function call with a number argument (bind a static property); the voice folders are
  named after languages, so the targets keep them out of the resource index; a tray icon's GUID belongs to the first
  executable path that used it (`TrayIconHost` falls back to an id), so keep the Debug and Release GUIDs apart and never
  change one; Native AOT links through the Visual Studio tools that `vswhere.exe` finds, which a plain shell may not
  have on its `PATH` (`screenshots.ps1` adds the installer's); an unpackaged build in the `obj\` and `bin\` a packaged
  publish left dies at start (a XAML parse failure, 0xC000027B), since the incremental build keeps what it made for the
  package, so `screenshots.ps1 -Configuration Release` builds in `obj\screenshots\<platform>` (`--artifacts-path`),
  emptied first.

### `windows/screenshots.ps1`

The WinUI app's windows in light and dark (`ScreenshotCapture`), in one language (`-Language`) or several in one launch
of the app (`-Language it,de`, each into `<out>/<lang>`; an app on an older Laching launches once per language), from
the Debug build or the Native AOT one (`-Configuration Release`); fails on the problems the run reports, or when the app
stops before the end of it. `-Only` runs hidden (cloaked windows, shot as soon as they have drawn), so the PC stays
usable, with `-Front` for active windows, and copies only the shots it names. `-Skin id` wears that skin, as
`screenshots.sh --skin` does, and `-Themes` names the themes to shoot, as `--themes` does; each window opens once for
light and dark. `-Contrast` runs again in the machine's contrast theme and turns it off afterwards (one such run at a
time on the PC, after any other screenshot run there ends; a PC already in one shoots only it). A Debug build that is
packaged (`WindowsPackageType` MSIX, for an app that needs package identity) is registered from its build output as a
development package (Developer Mode) and started in it, its settings passed as `LACHING_SCREENSHOT_…=value` arguments
(`ScreenshotCapture.Setting`).

### `windows/check-layout.ps1`

`screenshots.ps1` in every language (or the ones named), hidden after one build, the languages dealt among `-Jobs` runs
side by side (a packaged app's one run), each launching the app once for its share, naming the languages whose text
doesn't fit; each language's shots and `problems.txt` in `build/layout-check/<lang>` (or `-Out <folder>`).

### `windows/store-release.ps1`

The Microsoft Store release in one command (Windows PowerShell 5.1 or 7): per platform of `windows.release.platforms`
(x64 and ARM64) the unsigned Native AOT package, checked native and installed fresh and started (`smoke-package.ps1`; up
to 60 s for x64 emulated on ARM64; refuses without Developer Mode unless `-NoSmoke`), each next publish running in the
background during the previous package's check (`<out>/logs/publish-<name>.log`), `-Screenshots` every window from a
screenshot-mode package (`-ScreenshotNames store` or names for fewer), then one
`<windows.release.bundle>_<version>_x64_arm64.msixbundle` (makeappx); `-Store draft|submit` pushes it
(`store/push-microsoft.py`), `-Tag`/`-TagOnly` tags `win-<version>`. Each line is led by `[m:ss]` since the start; the
run ends with each step's time. The version is `Package.appxmanifest`'s, refused when its tag exists. The reusable
workflows run it in CI.

### `windows/smoke-package.ps1`

Installs a built `.msix` the way a customer gets it and checks it opens its window within `-Seconds` and is still up
`-Hold` seconds later (`-Keep` leaves it for the hand checks, `-SideBySide` next to a Store copy); `-Screenshots
<folder> [-Language] [-ScreenshotNames]` runs the screenshot run in the installed package instead (a Release publish
with `-p:ScreenshotMode=true`); `-CheckOnly` only checks the package is native.

### `windows/check-winui.py`

WinUI code that runs in Debug but breaks the Native AOT build or the XAML compiler (each rule in its docstring).

### `windows/assets.py`

The MSIX logos and `AppIcon.ico` (and the Debug build's) from the app icon; `--check`. **The images** come from the
Mac's app icon: `python3 laching/tools/windows/assets.py` after the icon changes (`windows.assets`), and `verify.sh`
checks them.

### `windows/vm.sh`

On a Mac, the WinUI app built and checked in a Parallels Windows VM from the working tree as it is (uncommitted changes
too): `build` (x64 and ARM64), `test`, `screenshots <folder>` (the PNGs back on the Mac), `check-layout [lang…]`
(`check-layout.ps1`, the shots back in `<windows.dir>/build/layout-check`), `smoke`, `run -- <command>`, `status`.
Without prlctl or the VM it says so in one line and exits 3. One run at a time on the VM, from any app or worktree:
another waits, naming the run it waits for (its command and checkout); `status` names it and exits 4, so a caller can
skip instead.

`laching/tools/windows/vm.sh build` mirrors the working tree (what git sees, uncommitted changes and submodules
included) into a Windows VM in Parallels Desktop and builds the app there, Debug, for x64 and ARM64, so a Mac change is
ported and built in the same session. `screenshots <folder>` runs the screenshot script there and copies the PNGs into
the Mac folder, `check-layout [lang…]` runs `check-layout.ps1` there (one build, the languages side by side, all of them
unless named) and copies each language's shots and `problems.txt` back into `<windows.dir>/build/layout-check/<lang>`,
`smoke` publishes the Native AOT package for the VM's platform and runs `smoke-package.ps1` on it (`-- -SideBySide`
where the Store copy is installed), `test` runs the test projects and `run -- <command>` anything else in the copy;
arguments after `--` go to the command. The copy is incremental (only changed files travel, `bin` and `obj` stay), and
`verify.sh` builds through it when the VM is there. The VM needs the .NET SDK, the Visual Studio Build Tools with the
x64 and ARM64 C++ tools (Native AOT), Developer Mode (screenshots and smoke install packages), Parallels Tools, a user
signed in, and the Mac's files shared (Parallels' "All Files", `\\Mac\AllFiles`). `LACHING_VM`, `LACHING_VM_SHARE` and
`LACHING_VM_DIR` change the VM's name, the share and the folder the copies go in (`%USERPROFILE%\laching-vm`).

### `windows-release.yml` and `windows-app.yml`

`../.github/workflows/windows-release.yml` and `windows-app.yml` are reusable workflows (`workflow_call`) an app's own
short workflows call at the release it pins: the Store release (both packages, the bundle as the artifact, the optional
push, the tag) and the WinUI build check (the traps, the tests, both packages, every window).

**The Store release** is `store-release.ps1` on a PC, or the app's two short workflows in CI, each calling Laching's
reusable one at the release the app pins (the `@vX.Y.Z` must be `laching.tag`; `companion.py bump` moves it with
the other pins, and `pins` and `conform` say when it lags):

```yaml
# .github/workflows/windows-release.yml: inputs hosted (boolean), tag (boolean), store (none, draft, submit)
permissions:
  contents: write
jobs:
  release:
    uses: vmihalachi/laching/.github/workflows/windows-release.yml@vX.Y.Z
    with:
      hosted: ${{ inputs.hosted }}
      tag: ${{ inputs.tag }}
      store: ${{ inputs.store }}
    secrets: inherit   # MS_STORE_TENANT_ID, MS_STORE_CLIENT_ID, MS_STORE_CLIENT_SECRET for a push
# .github/workflows/windows.yml
jobs:
  app:
    uses: vmihalachi/laching/.github/workflows/windows-app.yml@vX.Y.Z
    with:
      hosted: ${{ inputs.hosted }}
      languages: en,de
```

`tests/fixture-app/.github/workflows` has both in full. An app whose package needs another submodule (recorded voices)
passes `submodules: true`; the self-hosted runner's labels are `runner`. The skill is `release-windows`, the app's own
facts (the Store ID's history, past certification reports) in `.agents/notes/release-windows.md`.
