# Laching for Kotlin

`kotlin/` is Laching's Kotlin side: a Gradle build of its own that the Android apps include from their `laching/`
submodule. Nothing is published to Maven; the apps pin the submodule's tag. It ports `LachingCore`, the store and the
testing fakes, and adds the Android services; it has no companion yet.

| Module | Package | What it holds |
|---|---|---|
| `:core` | `laching.core.*` | Pure JVM, tested on any machine: `scheduling/`, `random/`, `localization/` (keys and plural rules), `settings/`, `store/` (the purchase store), `drawing/`. `Laching.VERSION` is generated from `gradle.properties`' `lachingVersion` before each compile. The tests replay the scheduler vectors (`conformance/scheduler`). |
| `:android` | `laching.android` | The Android half of the shell, thin on purpose: Play Billing behind `StoreBackend`, SharedPreferences behind `SettingsStore`, exact alarms and notification plumbing for the reminders, the reduce-motion check, the screenshot run and the launch extras the tools pass. Ships no strings and no resources: the app injects every piece of copy. Minimum Android 8 (API 26). |
| `:testing` | `laching.testing` | The mocks (`MockClock`, `MockIdle`, `MockBusy`, `ScriptedRandom`…, the store backend) and the conformance vector loader (`ConformanceRoot`, `ConformanceSupport`), as `Laching.Testing` on .NET. For the apps' test source sets; JVM only. |

`:core` and `:testing` share one setup in the root `build.gradle.kts`: JDK 17, explicit API, JUnit 5. The versions of
every plugin and library are in `gradle/libs.versions.toml`.

## In an Android app

The app's `settings.gradle.kts` includes the build with `includeBuild` and substitutes `laching:core`,
`laching:android` and `laching:testing` with its projects; the modules then depend on
`implementation("laching:core")`, `implementation("laching:android")` and `testImplementation("laching:testing")`.
The snippet, and the `android` section `companion.json` gets: [`docs/getting-started.md`](../docs/getting-started.md),
step 7. The included build is `../laching/kotlin` unless `local.properties` names another in `laching.kotlin.dir`,
which `companion.py link` writes to build against a Laching checkout and `unlink` takes out again
([`tools/README.md`](../tools/README.md#moving-to-a-release-or-trying-one-first)).

## Commands

```sh
cd kotlin && ktlint "**/*.kt" "**/*.kts" "!**/build/**"   # the version kotlin.yml pins (KTLINT_VERSION); ktlint -F fixes
cd kotlin && JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew :core:test :testing:test   # a JDK 17; replays conformance/scheduler
cd kotlin && ./gradlew :android:assembleDebug   # needs the Android SDK (ANDROID_HOME); otherwise kotlin.yml builds it
```

The style is `kotlin/.editorconfig` (`ktlint_official`, 120 columns). `scripts/verify.sh` runs ktlint when `kotlin/`
changed (and skips it for any version but the one `kotlin.yml` pins), and, without `--fast`, the `:core` and
`:testing` tests when `kotlin/` or `conformance/` changed, on a JDK 17 from `JAVA_HOME`, else Homebrew's
`openjdk@17`, else the `java` on `PATH`. `kotlin.yml` runs ktlint, those tests and the `:android` debug build on
hosted Ubuntu.

Porting a Swift change here (where each twin lives, the naming, the vectors): the `port-to-kotlin` skill,
[`.agents/skills/port-to-kotlin/SKILL.md`](../.agents/skills/port-to-kotlin/SKILL.md).
