---
name: conformance
description: Change behavior that a Laching companion app's conformance vectors pin — its phrase bank, picker cases, quiet timelines, settings, character poses or plural rules — or fix a stale-vector failure in the app's ConformanceTests or the Windows `dotnet test`.
---

# Changing vector-tested behavior

The Swift code is the reference implementation. `<conformance.dir>/` (companion.json) holds JSON vectors generated
from it by the app's `ConformanceTests` through `LachingTesting`'s generators (`PhrasesVector`, `PickerVector`,
`QuietTimeline`, `CharacterPoseVector`, `SettingsVector.base`…), and the Windows port replays every one through
`Laching.Testing`'s replay bodies (`PhrasesReplay`, `PickerReplay`, `QuietReplay`, `CharacterReplay`,
`SettingsReplay`…). Formats: Laching's `conformance/README.md` (read one vector as a sample, never all of them).
Never hand-edit a vector (the hook refuses). The mechanism itself (picker rules, cadence, quiet rules, coordinator,
desktop presence, the rig) is pinned by Laching's own vectors; the app pins its content over that mechanism.

## Steps

1. Change the Swift code in the app's core (`mac.core`) and its unit tests. Keep pure logic pure: no clock, UI,
   speech or system calls; time, randomness and system state come in through `PhraseContext`, `Snapshot`, the
   settings store and injected providers.
2. Add a case to the app's conformance case lists for the new behavior.
3. `laching/tools/conformance.sh` regenerates the vectors (any Swift toolchain; the core is platform-neutral; from a
   Linux session without one, dispatch the app's Regenerate vectors workflow and pull its commit). Commit them in the
   same change: `ConformanceTests` fails when they're stale.
4. Port to the Windows core until its `dotnet test` passes (`windows-port` skill).
5. A behavior change in Laching's mechanism belongs in Laching (its `add-scheduler-behavior`, `port-to-csharp` or
   `port-to-kotlin` skill), then a Laching release the app pins (`laching.tag`).
6. `scripts/verify.sh` runs everything that applies.

## Test setup

Use `LachingTesting`'s fakes (`MockClock`, `MockIdle`, `MockBusy`, `ScriptedRandom`) and `InMemorySettingsStore`;
in C#, `Laching.Testing`'s. Picker and rules tests build a `PhraseContext` or `Snapshot` by hand.
