---
name: port-to-kotlin
description: Use when a Swift change in LachingCore (scheduling, random, localization, settings, drawing), LachingStore or LachingTesting's fakes needs its Kotlin twin in kotlin/, when an Android service in kotlin/android changes, or when gradle :core:test fails on a scheduler vector after a Swift change. Covers the three modules, where each twin lives, the naming, the vectors, ktlint and gradle.
---

# Port a Swift change to Kotlin

`kotlin/` is a Gradle build of its own that the Android apps include from their `laching/` submodule (nothing is
published to Maven). It ports `LachingCore`, the store and the testing fakes, and adds the Android services; it has no
companion yet. Port in the same change as the Swift edit.

## The modules

| Module | Package | What it holds |
|---|---|---|
| `:core` | `laching.core.*` | Pure JVM, tested on any machine: `scheduling/`, `random/`, `localization/`, `settings/`, `store/`, `drawing/`. |
| `:android` | `laching.android` | The Android services: Play Billing, the reminder alarm and notification, reduce motion, launch context, settings in SharedPreferences, the screenshot run. Minimum Android 8 (API 26). |
| `:testing` | `laching.testing` | The fakes (`MockClock`, `MockIdle`, `MockBusy`, `ScriptedRandom`…), `ConformanceRoot` (finds `conformance/`) and `ConformanceSupport` (the vectors' dates and zones). |

## Where the twin lives

| Swift | Kotlin |
|---|---|
| `Sources/LachingCore/<Folder>/<Name>.swift` | `kotlin/core/src/main/kotlin/laching/core/<folder>/<Name>.kt` (the folder in lower case) |
| `Sources/LachingStore/<Name>.swift` | `kotlin/core/src/main/kotlin/laching/core/store/<Name>.kt`; the platform backend is `kotlin/android/…/PlayBillingBackend.kt` |
| `Sources/LachingTesting/Mocks.swift`, `Conformance/ConformanceRoot.swift`, `Conformance/ConformanceSupport.swift` | `kotlin/testing/src/main/kotlin/laching/testing/<Name>.kt` |
| `Sources/LachingTesting/MockStoreBackend.swift` | `kotlin/core/…/store/MockStoreBackend.kt` (apps use it in their preview modes, as .NET does); `laching.testing` re-exports it |
| `Tests/LachingCoreTests/<Folder>/<Name>Tests.swift` | `kotlin/core/src/test/kotlin/laching/core/<folder>/<Name>Tests.kt` |
| `Tests/LachingCoreTests/Conformance/` | `kotlin/core/src/test/kotlin/laching/core/scheduling/SchedulerConformanceTests.kt` |

Some files differ: `SchedulerState.kt` holds the decision and status types Swift keeps in `SchedulerConfig.swift`,
`SplitMix64.kt` is split out of `RandomSource.swift`, `PluralRules.kt` mirrors the C# `PluralRules` (Swift has
Foundation's), and `ResourceNames.kt` mirrors the names `tools/l10n.py` writes. `UserDefaultsSettingsStore` and
`StoreKitBackend` have Android counterparts in `:android`, not twins. `LachingSystem`, `LachingShell`,
`LachingCompanion` and `LachingCompanionUI` have no Kotlin port: don't invent one.

## Translation rules

- Same type and member names; acronyms are words (`SVGPath` ↔ `SvgPath`) and enum cases upper case
  (`.remind` ↔ `SchedulerDecision.REMIND`). Durations are `Double` seconds with a `Seconds` suffix
  (`interval` ↔ `intervalSeconds`). Config types are data classes of `val`s with the same defaults. Dates are
  `java.time.Instant`, and the calendar is a `ZoneId`.
- Explicit API mode: every public declaration says `public`. Its KDoc says what it does, needs and never does, as the
  Swift doc comment does.
- Same algorithm in the same order. `conformance/README.md` has the rounding rules.
- A programmer error is a `require` (an `IllegalArgumentException`) with the Swift message text
  (`docs/conventions.md`).
- JSON through kotlinx.serialization only.
- `:core` and `:testing` stay pure JVM; anything that imports `android.*` goes in `:android`. No network, no permission
  prompts, no strings.
- Kotlin replays the scheduler vectors only. A new scheduler config key is read as an optional with its default in
  `SchedulerConformanceTests.config()` (the `add-scheduler-behavior` skill).

## Verify

```sh
cd kotlin && ktlint "**/*.kt" "**/*.kts" "!**/build/**"   # the version kotlin.yml pins (KTLINT_VERSION); ktlint -F fixes
cd kotlin && JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew :core:test :testing:test   # a JDK 17; replays conformance/scheduler
cd kotlin && ./gradlew :android:assembleDebug   # needs the Android SDK (ANDROID_HOME); otherwise kotlin.yml builds it
```

`scripts/verify.sh` runs the first two when `kotlin/` or `conformance/` changed, finding a JDK 17 in `JAVA_HOME` or
Homebrew's `openjdk@17`. The style is `kotlin/.editorconfig` (`ktlint_official`, 120 columns).
