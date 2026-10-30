# Getting started

A new app on Laching, from zero: the packages first, then the tools (the submodule, `companion.json`, the wrappers,
the hook, the skills). `X.Y.Z` below is the release the app pins, the tag without its `v`.

## Install the packages

**Swift** (macOS 14+ and iOS 17+, the platforms `Package.swift` names), in `Package.swift` or Xcode's package list,
pinned to a tag:

```swift
.package(url: "https://github.com/vmihalachi/laching", exact: "X.Y.Z")
```

**.NET** (10), from NuGet.org. `Laching.Windows` and `Laching.Windows.Companion` need **Windows 11**: they target
`net10.0-windows10.0.22000.0` with `TargetPlatformMinVersion` 10.0.22000.0, so an app targets that or later (the rest
are `net10.0` and run anywhere):

```sh
dotnet add package Laching.Core
dotnet add package Laching.Companion  # the desk companion layer
dotnet add package Laching.Windows   # Windows 11; the tray, idle and busy services
dotnet add package Laching.Companion.Shell    # a companion app's shell, platform-free
dotnet add package Laching.Windows.Companion  # Windows 11, WinUI 3: that shell on Windows
dotnet add package Laching.Testing   # in test projects: fakes, fixtures, vector replays
```

**Kotlin**, for Android, is not on a Maven repository: the app's Gradle build includes it from the `laching/`
submodule (step 7 below; the modules: [`kotlin/README.md`](../kotlin/README.md)).

**Web**, for a small ASP.NET Core or Blazor site: the six `Laching.Web.*` packages, from NuGet.org. They share nothing
with the rest; what each does: [`web/README.md`](../web/README.md).

```sh
dotnet add package Laching.Web.Jobs
dotnet add package Laching.Web.AspNetCore
dotnet add package Laching.Web.Cloudflare
dotnet add package Laching.Web.Aspire        # in the AppHost
dotnet add package Laching.Web.Tailwind      # build only; nothing ships with the app
dotnet add package Laching.Web.Images
```

One version for all six (`LachingWebVersion`), so keep them on the same one. It moves only in a Laching release
that changed them, so it can trail Laching's tag.

## Adopt the tools

What each tool does: [`tools/README.md`](../tools/README.md).

1. **Submodule, pinned to the release.** Vendor Laching at the tag the app pins in SwiftPM (NuGet takes that release's
   `LachingVersion`, which can trail the tag):

   ```sh
   git submodule add https://github.com/vmihalachi/laching laching
   git -C laching checkout vX.Y.Z && git add laching
   ```

   The submodule is tools and skills only; the Swift and .NET code still comes from the package registries (the
   Kotlin build is the exception: an Android app includes it from the submodule, see step 7). `scripts/verify.sh`
   fails whenever the submodule's tag, `laching.tag`, the core `Package.swift`'s `exact:`, the XcodeGen spec's
   `exactVersion` and, with an `android` section, the submodule's `kotlin/gradle.properties` `lachingVersion` disagree,
   or a `Laching.*` NuGet version isn't that package's in the release (`python3 laching/tools/companion.py pins` shows
   them; `companion.py bump` moves them all). The NuGet packages share one version, `LachingVersion` in the
   submodule's `dotnet/Directory.Build.props`: it moves only when they changed, so it can trail the tag.

2. **`companion.json`** at the app's root, every section the app has (see the schema and the drafts in
   `tools/tests/*.companion.json`). Check it: `python3 laching/tools/companion.py validate`.

3. **Two-line wrappers**, so the documented commands keep working, e.g. `scripts/verify.sh`:

   ```sh
   #!/bin/sh
   exec "$(dirname "$0")/../laching/tools/verify.sh" "$@"
   ```

   and the same for `setup-linux.sh`, `conformance.sh`, `screenshots.sh`, `check-layout.sh`, `install-dev.sh` (the scheme's post-action);
   Python tools are called directly (`python3 laching/tools/l10n.py status`).

4. **The hook.** Claude Code, `.claude/settings.json`:

   ```json
   {
     "hooks": {
       "PreToolUse": [{ "matcher": "Read|Edit|MultiEdit|Write|NotebookEdit|Grep|Bash", "hooks": [
         { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/laching/tools/hooks/hook.py\" pre", "timeout": 10 }] }],
       "PostToolUse": [{ "matcher": "Edit|MultiEdit|Write|NotebookEdit|Bash", "hooks": [
         { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/laching/tools/hooks/hook.py\" post", "timeout": 60 }] }],
       "Stop": [{ "hooks": [
         { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/laching/tools/hooks/hook.py\" stop", "timeout": 180 }] }],
       "SessionStart": [{ "matcher": "startup", "hooks": [
         { "type": "command", "command": "[ \"$CLAUDE_CODE_REMOTE\" != true ] || \"$CLAUDE_PROJECT_DIR/scripts/setup-linux.sh\"", "timeout": 600 }] }]
     }
   }
   ```

   Codex, `.codex/config.toml` (`[features] hooks = true`): `PreToolUse`/`PostToolUse` with matcher
   `^(Bash|apply_patch)$` and `Stop`, each running
   `python3 "$(git rev-parse --show-toplevel)/laching/tools/hooks/hook.py" <pre|post|stop>`. GitHub Copilot,
   `.github/hooks/<app>.json`: `PreToolUse` and `PostToolUse` running `python3 laching/tools/hooks/hook.py pre|post`,
   and for Copilot's cloud coding agent `agentStop` running `hook.py cloud-stop` (the app's `verify.sh` on the branch,
   inside GitHub Actions only). What the hook guards and refuses:
   [`tools/README.md`, *`hooks/hook.py`*](../tools/README.md#hookshookpy).

5. **Skills.** Symlink the generic ones next to the app's own:

   ```sh
   for s in localization add-phrase add-setting conformance voice windows-port release-mac release-ios release-windows review-invariants; do
     ln -s "../../laching/tools/skills/$s" ".agents/skills/$s"
   done
   ```

   What they read from the app: [`tools/README.md`, *`skills/`*](../tools/README.md#skills). A skill of Laching's is
   never copied into the app: `companion.py conform` fails on a copy
   ([*Staying on Laching's tools*](../tools/README.md#staying-on-lachings-tools)).

6. **The app's own files** the tools read: `l10n.rulesFile` (transcreation rules for this app), the voice config and
   direction (`voice.config`, `voice.direction`), the sounds recipes (`sounds.recipes`), the store captions, the
   character design folder (`characters.design`).

7. **Android apps.** The Kotlin side (`kotlin/`: `:core`, the pure mirror of `LachingCore` and the store that replays
   the scheduler vectors; `:android`, the app plumbing; `:testing`) is not published to a Maven repository: the app's
   Gradle build includes it from the submodule, and `companion.py link` points it at a checkout instead through
   `local.properties`. In the app's `settings.gradle.kts`:

   ```kotlin
   val localProperties = java.util.Properties().apply {
       val file = rootDir.resolve("local.properties")
       if (file.isFile) file.inputStream().use { load(it) }
   }
   val lachingKotlin = localProperties.getProperty("laching.kotlin.dir") ?: "../laching/kotlin"
   includeBuild(lachingKotlin) {
       dependencySubstitution {
           substitute(module("laching:core")).using(project(":core"))
           substitute(module("laching:android")).using(project(":android"))
           substitute(module("laching:testing")).using(project(":testing"))
       }
   }
   ```

   and `implementation("laching:core")`, `implementation("laching:android")`, `testImplementation("laching:testing")`
   in the modules that use them. `companion.json` gets an `android` section (`dir`, `core`, `app`, `applicationId`,
   `devApplicationId`, `strings`, the `.xml` folder `l10n.py export-android` writes); the hook then runs ktlint over the
   Kotlin files an agent edits (`brew install ktlint`, or `setup-linux.sh` on Linux, which also installs a JDK 17) and
   reminds it about Android parity, `verify.sh` lints the changed Kotlin files and, when `:core` or the vectors change,
   runs `./gradlew :<android.core>:test` (JDK 17 and `ANDROID_HOME` inside the command, never in a shell profile), and
   `screens.py`, `check-layout-android.py` and `store-screenshots.py --canvas android-…` cover the screens.

Later, moving the app to a newer release, or building it against a Laching checkout first:
[*Moving to a release, or trying one first*](../tools/README.md#moving-to-a-release-or-trying-one-first).
