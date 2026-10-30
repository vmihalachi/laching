# Review checklist

The rules a Laching companion app's diff can break without a test failing: the generic part, shared by every app.
Each app continues the list in `.agents/review-appendix.md` (its monetization rules, words its copy never uses, its
palette and type tokens, its parity doc). Paths and names come from `companion.json`. Report only what the diff
actually breaks. Tooling and docs are out of scope except item 13.

## Privacy

1. **No camera or microphone, ever.** Nothing starts an `AVCaptureSession`, calls `requestAccess`, uses
   `AVAudioEngine` input or `MediaCapture`, and the entitlements and `Package.appxmanifest` gain no camera,
   microphone or network capability. Call detection only reads whether devices are in use (Laching's
   `SystemBusyStateProvider`). Check: added lines matching `AVCapture|requestAccess|AVAudioEngine|MediaCapture|
   device\.camera|device\.microphone|webcam|microphone`.
2. **No network.** No `URLSession`, `URLRequest`, `NWConnection`, `WKWebView` loading remote URLs, `HttpClient` (C#)
   or third-party SDKs; the store backend is the only exception. Landing: no new external script, font, image or
   `fetch`. No analytics, telemetry or crash reporting.
3. **Nothing persisted beyond settings.** No new file, database or defaults key that records what the character said,
   when, or what the user did; the recent-phrase list stays in memory.

## Stable identifiers

4. Phrase ids, `Tag`, flavor, skin and voice raw values, `Channels` names, settings keys and store product ids are
   unchanged (renames show up as a changed string literal in the bank, the settings keys or the product list).
   Adding new ones is fine; a renamed settings key keeps the old one readable.

## Pure logic stays pure

5. The core package (`mac.core`) and the Windows core gain no `Date()`, `Timer`, `DispatchQueue`, `NSApp`, SwiftUI,
   AVFoundation, speech, WinUI or `UserDefaults.standard`. Time, randomness and system state come in through
   `PhraseContext`/`Snapshot` values, the `SettingsStore` and injected clocks, providers and random sources.
6. Cadence numbers, daypart boundaries and the no-repeat window stay in Laching's constants, not literals in views or
   the app model.
7. Behavior the vectors pin (bank, picker cases, quiet timelines, settings, character poses, plurals) changed → the
   vectors are regenerated in the same change and the Windows core follows (`conformance` skill).

## Text

8. No user-facing source-language text in a Swift `String` literal: it's `Text("…")`, `String(localized:comment:)` or
   a `LocalizedStringResource`; phrases are catalog keys. `Text(verbatim:)` only for symbols, numbers and the names in
   `l10n.keepNames`. Numbers, dates and lists use `.formatted()`. On Windows, text goes through `Loc` or
   `{l:Loc Key='…'}`.
9. New keys are translated into every language in `languages.all` with all plural categories (`l10n.py check`
   passes), except a language-scoped phrase's keys outside its languages. Landing keys exist in every locale file.
10. Copy makes no health, wellbeing or productivity claims and never scolds the user; the landing page and store
    listings describe only shipped features.

## UI and design

11. Colors come from the app's palette (`CompanionPalette` on the Mac, palette resources on Windows), not system
    grays or hex literals; spacing uses `Space` steps; animations respect Reduce Motion and the character freezes on
    its still pose.
12. User-visible Mac changes (`hooks.macUI`: screens, copy, flows, phrases) have a matching change under the Windows app
    (`hooks.windowsUI`) and, when `hooks.androidUI` is set, under the Android app, or the diff says why there's no
    counterpart.

## Repo hygiene

13. Generated files change only through their generator: `project.pbxproj` with the XcodeGen spec, the vectors with
    the Swift code (`laching/tools/conformance.sh`), `.resw` with the String Catalog (`l10n.py export-windows`), the
    phrase keys with `phrases.py sync`, sounds with `laching/tools/synth.py`, the dev icon with `dev-icon.swift`,
    character art with `laching/tools/character.py`, the voices folder with `voice.py render`.
14. Concurrency: code touching the app model, window manager, speaker, `DeliveryPerformer` or `SignalHub` stays on
    `@MainActor`.

## App appendix

15+. The app's own items, from `.agents/review-appendix.md`.
