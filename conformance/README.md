# Conformance vectors

Language-neutral test data that keeps the Swift package, the .NET port and the Kotlin side behaving the same. The Swift code
is the reference: every file here is **generated** by `Tests/LachingCoreTests/Conformance` (`scheduler/`) and
`Tests/LachingCompanionTests/Conformance` (`companion/`), and replayed by `dotnet/Laching.Core.Tests/Conformance`,
`dotnet/Laching.Companion.Tests` (from sequence 3) and, for `scheduler/`, `kotlin/core`'s `SchedulerConformanceTests`
(no Companion in Kotlin yet).

**Never edit a vector by hand.** Change the Swift code, then regenerate:

```sh
scripts/conformance.sh        # LACHING_WRITE_CONFORMANCE=1 swift test --filter 'ConformanceTests/testWriteVectors'
```

Each target's `ConformanceTests` fails whenever the committed files differ from what the Swift code generates, and
replays every committed vector. To add a case, add it to the `cases` list in
`Tests/LachingCoreTests/Conformance/SchedulerVectors.swift` or `Tests/LachingCompanionTests/Conformance/CompanionCases.swift`
and regenerate. `companion/characters/<skin>.json` is also what `tools/character.py` writes from
`Tests/Fixtures/characters/` (`scripts/fixture-art.sh`); the Swift generator writes the same bytes.

Files are byte-exact (`.gitattributes` turns off line-ending conversion). Ports parse the JSON and compare values.

## Shared conventions

- **Dates** are ISO 8601 with an offset (`2026-09-28T10:00:00+03:00`, `Z` for UTC), written in the vector's
  `timeZone`, with milliseconds only when the date has a fraction of a second.
- **`timeZone`** is an IANA ID. Every calendar computation (days, hours, weekdays, midnight) uses the Gregorian
  calendar in that zone. Weekdays are 1 = Sunday … 7 = Saturday.
- **Durations** are whole seconds (`…Seconds`) or minutes (`…Minutes`). Minutes of the day (`activeStartMinute`)
  count from local midnight.
- **Optional values** are either written as `null` or left out; treat both as "no value".

## `scheduler/*.json`

`ReminderScheduler`: a config, then a timeline of steps, each with the expected result and state after it.
The first 37 vectors are mybackhurts' `conformance/scheduler`, byte for byte: the package behaves exactly like
the code it was extracted from. The `jitter-*` vectors add the one new behavior.

- `start`: the clock when the scheduler is created. `initialState`: its state right then. `seed` (optional): the
  `SplitMix64` seed of the scheduler's random source; absent when the vector uses no jitter.
- `config`: `intervalSeconds`, `activeStartMinute`, `activeEndMinute` (end before start means overnight),
  `activeWeekdays`, `quietHours` (local hours 0–23), `idleThresholdSeconds`, `postponeDuringCalls`,
  `postponeDuringFullScreen`, `busyGraceSeconds`, `afterCallFallbackSeconds`, and `intervalJitterSeconds`
  (optional, absent when zero). Each new interval is `intervalSeconds` plus `floor((2u − 1) × jitter + 0.5)`
  seconds, where `u` is the next unit from SplitMix64 (top 53 bits over 2^53) and `jitter` is
  `min(intervalJitterSeconds, max(intervalSeconds − 1, 0))`, so no interval is shorter than a second; with zero
  jitter the source is never read.
- `steps[].op`:
  - `ticks`: repeat `count` times: advance the clock by `everySeconds` (0 or missing: don't), then call `tick()`
    with `idleSeconds` since the last input and the `busy` reasons (`call`, `fullScreen`) active.
  - `setTime` (`time`), `snooze` (`minutes`), `snoozeFor` (`seconds`), `snoozeUntilAfterCall`, `pauseFor` (`seconds`), `pauseUntil` (`time`),
    `pauseUntilEndOfToday`, `resume`, `sessionCompleted`, `updateConfig` (`config`), `restore` (`time`: the next
    fire date an earlier run scheduled; ignored while paused, outside the active hours or when not after now),
    `reminderDelivered` (the system delivered a reminder scheduled ahead: unanswered, so `snooze` reminds in its minutes).
  - `isWithinActiveHours`: ask about `time`; the answer is `expect.result`.
- `steps[].expect`: `decisions` (for `ticks`: the 1-based ticks that returned `remind` or `postpone`; every other
  tick returned none), `result`, and `state`: `now`, `status` (`{type: "scheduled"|"paused", date}`,
  `{type: "outsideActiveHours"|"away"}` or `{type: "postponed", reason}`), `nextFireDate`, `pausedUntil`,
  `isAway`, `isHolding`, `isWaitingForCall`, `endOfToday`.

## `companion/`

The companion layer (`LachingCompanion`) over the fixtures in `LachingTesting`: `PhraseBank.fixture`,
`CompanionDefaults.fixture` (skin `fixture-vector`, flavors `a`/`b`/`c`) and the two fixture skins. These pin the
mechanism; each app keeps its own content vectors in the same formats, written by `LachingTesting`'s generators
(`PhrasesVector`, `PickerVector`, `QuietTimeline`, `CharacterPoseVector`, `SettingsVector.base(defaults:)`…) over its
own bank and art. The formats are sunnysays' (its `conformance/README.md`), with the additions marked **new**; a new
field is written only when it isn't the default, so sunnysays' files read the same.

An app's case lists are the standard ones plus its own: `QuietTimeline.standardCases(base:dst:)` (17 timelines, the
last one in the app's DST city) and `PickerVector.standardCases(...)` (15 cases, the flavor ones under the app's
names), then the cases only it has (its button, its languages, its skins). The app's content as a `CompanionVectors`
type gives `ConformanceSuite.companion(_:root:regenerateHint:)` the whole suite; on Windows, `CompanionReplay`
replays each file kind. The lists here are the same standard ones over the fixtures, plus the cadence, the desktop and
the fixture's own picker and quiet cases.

| File | Pins |
|---|---|
| `phrases.json` | The bank as data: `phrases[]` in bank order with `id` (stable, never renamed), `kind` and its fields: `line` with `key` (`phrase.<id>`) and `english` (the catalog's source text, whatever its language: the field keeps its name), `tune` with `sound`, or **new** `sequence` with `keys` (`phrase.<id>.1`, `.2`, …) and `parts` (the source text of each beat); then `flavor` (absent for tunes), sorted `tags`, `weight`, sorted `skins` (`[]` = every skin) and **new** `languages` (sorted BCP-47 base codes; absent = every language). Then `tags` (all, in declaration order), `flavors` (the app's, in its order), `eventTags`, `dayparts` (tag → first and last hour), `weekdays` (tag → calendar weekdays) and `recentWindow`. |
| `picker/*.json` | `PhrasePicker`: a `context` (`hour`, `weekday`, `event` or absent, `chimeEnabled`, `recentIDs` oldest first, sorted `flavors` (`[]` = every flavor), sorted `excludedIDs`, `skin`, **new** `language`: absent means `en`), the `candidates` in bank order, and `draws[]`: for each uniform `unit` in [0, 1) the `id` picked (absent when nothing qualifies). The candidates drop phrases not in the context's language first (by base: `pt-BR` fits `pt`). |
| `cadence/*.json` | `ChatterCadence`: `settings` (a frequency preset plus the timing settings) → `config`, the `SchedulerConfig` it builds (every field, in seconds), and `fireMinutes`: the minutes after `start` at which a scheduler seeded with `seed` returned `remind` when ticked once a minute for `minutes` minutes with nobody busy or away. sunnysays' five cases, byte for byte. |
| `quiet/*.json` | The whole pure flow through `CompanionCoordinator`: `settings` (channels, frequency, hours, weekdays, quiet hours, hush in full screen, the three comment toggles, a stored `shushedUntil`; everything else is the fixture defaults), `seed`, `start`, **new** `language` (absent = `en`), then `steps[]`. Each step advances the clock by `advanceSeconds` and performs `action` (`tick`, `poke`, `wake`, `shush` with `argument` hour / restOfToday / untilISay, `event` with a trigger, `setFrequency`, `setChannels` with comma-separated names) `repeat` times (absent = once) with the `snapshot` (`busy` names, `idleSeconds`, `isScreenLocked`, `isOutputMuted` or null, `isSpeaking`, `notificationsAuthorized`); it records the `deliveries` (repetition `index`, `at`, phrase `id`, `trigger`, `speak`, `chime`, `notify`), then the `mood`, `isShushed` and `shushedUntil` after the step. A sequence is delivered and remembered by its phrase id like a line (`sequence-delivered`). The scheduler's jitter is seeded with `SplitMix64(seed)` and the picks with `SplitMix64(seed + 1)`. |
| `desktop.json` | `DesktopPresence`: `reappearDelaySeconds`, `hourHideSeconds`; `timelines[]` (`name`, `steps[]`): each step at `t` seconds after Unix 1 800 000 000 first applies `hideForSeconds` (hide until `t` + that) and `showNow` when present, then calls `update` with `enabled` and a snapshot with the `busy` names, and expects `shown`; `bubbles[]`: `text` → `seconds` the bubble lingers (characters counted as Unicode scalars); **new** `partDelays[]`: `spoken`, `previousPart` → `seconds` before a sequence's next part (`PhraseTiming.partBeat` after a spoken part, else the previous part's bubble time). |
| `settings.json` | `SettingsVector.base`: every generic preference's first-launch value (`defaults.<property>`; the shortcut as key code, Carbon modifier mask and key label) and storage key (`keys`, `keyOrder`), the `resettable` keys, the `frequencies` table (name → interval and jitter seconds), the `channels`, `appearances` and `shushes` names, the `desktopSizes` table (name → height in points), and the constants `idleThresholdSeconds`, `busyGraceSeconds`, `welcomeBackCooldownSeconds`, `recentCapacity` and **new** `recentWindow`, `partBeatSeconds`, `reappearDelaySeconds`, `hourHideSeconds`. The app adds its own keys and its `skins` list. |
| `characters/<skin>.json` | A skin's compiled art, `CharacterArtJSON` (the format `tools/character.py --out` writes): `skin`, `viewBox` [w, h], `pivots` (name → [x, y]), `parts` (name → an array of shapes, or **new** an image `{"image": "<skin>.<part>", "frame": [x, y, w, h]}` in view-box units), `glyph` (`size`, `frames`: frame id → shapes) and `rig`: `period`, `stillTime`, `always` (parts drawn in order), `head`/`stem`/`pulse` groups (`pivot`, `parts`, `follow`), `blink` (`duration`, `intervals`, `closedEyes`), `moods` (mood → `eyes`, `mouth` [ids], `mouthRate`, **new** `headExtras` and `overlays` [part ids] in place of `cheeks` and `zz`, `blink`, `droop`, `sink`, `sway`, `swayPeriod`, `pulse`; every field present, except the optional `restMouth`, the closed mouth between words when a recorded clip's envelope drives the mouth, written only when set), `glyph` (mood → frame ids) and **new** `bubble` (`pivot`, `tuck`: a fraction of the width). A shape is `d` (`["M", x, y]`, `["L", x, y]`, `["C", x1, y1, x2, y2, x, y]`, `["Z"]`), `fill` and `stroke` (paint roles or null), `strokeWidth`, `opacity`, `evenOdd`. Sorted keys, two-space indent, numbers to 3 decimals, paths and scalar lists on one line. |
| `characters/<skin>-poses.json` | `RiggedCharacter` on that art: `poses[]` (`mood`, `time` in seconds, the `pose`: `headAngle`, `sink`, `pulse`, `eyes`, `mouth`, **new** `headExtras` and `overlays`, `blinking`) for every mood at 0, 1, 4.15 and 0.2 s plus talking at 0.05 s; `still[]` (each mood's still pose); `glyph[]` (every mood's glyph frames); and for six poses, `shapes[]` (the resolved shapes; only when the skin has vector parts, image parts skipped) and **new** `layers[]` (only when it has image parts: each placed part's `name`, `transform` [a, b, c, d, tx, ty] and `kind`, `shapes` or `image`, in drawing order: `always` parts, then head extras, eyes and mouth under the head, then overlays untransformed). Numbers are rounded to 6 decimals and each path is one string (`"M x y C x1 y1 x2 y2 x y … Z"`), so the file is identical whichever platform's math library made it; replay within 1e-6. Transforms only rotate, translate and scale uniformly, so a raster part never shears.; when the talking rig has a `restMouth`, `voiced[]` (`level` 0–3, `time` 0.2 s, the talking `pose` with that mouth level: 0 the rest mouth and no pulse, 1–3 the talking mouths least open first and the pulse × level / 3), absent otherwise |

The fixture cases cover what sunnysays' did, re-expressed over fixture ids, plus `sequence-picked`,
`skin-restricted-dropped`/`-kept` and `language-scoped-dropped`/`-kept` (picker) and `sequence-delivered`,
`italian-afternoon` and `say-more-while-speaking` (quiet: a button press is delivered mid-line, where the shell cuts
the line being said; any other trigger still waits for the speech to end).
