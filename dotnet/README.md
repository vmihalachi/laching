# Laching for .NET

Building blocks for little companion apps that live in the tray: the parts every such app needs and nobody wants to
write twice. These are the .NET packages of [Laching](https://github.com/vmihalachi/laching), which is Swift for macOS
and iOS, .NET for Windows and Kotlin for Android. The Swift code is the reference; the .NET side replays its
conformance vectors, so both behave the same. MIT.

Laching is built for [mybackhurts](https://mybackhurts.app), [sunnysays](https://sunnysays.app) and
[Mister Pagliacci](https://pagliacci.app). It is `0.x`, so minor versions may change public types.

**No support.** Laching is shared as is, for anyone to use, but it is built for these apps: its
[issues](https://github.com/vmihalachi/laching/issues) are the owner's own plans, issues and pull requests from
others aren't monitored, and releases follow what the apps need.

This README is packed into each package (`dotnet/README.md` in the repository), so every link in it is absolute.

## The packages

All six share one version. `Laching.Windows` and `Laching.Windows.Companion` need **Windows 11**: they target
`net10.0-windows10.0.22000.0` with `TargetPlatformMinVersion` 10.0.22000.0, so an app targets that or later. The rest
are `net10.0` and run anywhere.

```sh
dotnet add package Laching.Core
dotnet add package Laching.Companion  # the desk companion layer
dotnet add package Laching.Windows   # Windows 11; the tray, idle and busy services
dotnet add package Laching.Companion.Shell    # a companion app's shell, platform-free
dotnet add package Laching.Windows.Companion  # Windows 11, WinUI 3: that shell on Windows
dotnet add package Laching.Testing   # in test projects: fakes, fixtures, vector replays
```

| Package | What it does |
|---|---|
| `Laching.Core` | Pure logic. `ReminderScheduler`: an interval timer with active hours and weekdays, quiet hours, an idle reset, a hold while the user is busy with a grace period after, snooze and pause, on an injected clock. A seeded `SplitMix64` random source, plurals and settings. `Store/`: a purchase store for one-time unlockables and tips, with the backend behind an interface so tests use `MockStoreBackend`, and `ReviewLifecycle` (when an app may ask for a review, once). |
| `Laching.Companion` | The pure core of a desk companion: a phrase bank and picker (lines, jokes told in beats, tunes; scoped by skin, flavor and language), chatter cadence, quiet rules and mood, settings, a coordinator that replays a whole day from a seed, desktop presence, skins and character art. It loads the JSON the Swift side writes (`PhraseBank.Parse`, `CharacterArt.Parse`). |
| `Laching.Windows` | The real providers and the tray plumbing. Idle time, **call detection without permissions** (whether another app is using the camera or the microphone, read from the capability consent store, never by opening a device), full-screen detection, power, output mute, screen lock and appearance. `TrayIconHost`, `AppNotifier<TAction>`, `StartupService(taskId)`, `LocalSettingsStore`, `SingleInstance.Claim(key)`, `WavPlayer`, the `.resw` lookup `Loc`, and `AppFeedback` for reviews and contact. |
| `Laching.Companion.Shell` | The platform-free half of a companion app's shell, written against small interfaces so its tests run on any OS: `CompanionAppModel<TWindow, TSettings>`, the menus as data, speech in beats and recorded voices, the delivery performer, the signal hub, the desktop layout and a screenshot run's sample environment. |
| `Laching.Windows.Companion` | That shell on Windows, in WinUI 3: the app's `Application`, the tray status item with its glyph and panel, the windows, the speaker, chime and notifier, the character on the desktop, and the character canvas, speech bubble, status pill, chrome, Settings rows, Support window and screenshot capture. |
| `Laching.Testing` | Fakes for every interface, fixtures (a phrase bank, defaults, a vector and a raster skin) and the conformance vectors' helpers, for an app's own test projects (no test framework needed: each check returns its problems). Also the shell's fakes (`FakeHost`, `ManualTimers`, `FakeSpeaker`…), for testing an app model without a screen. |

The packages ship no strings, product ids or window lists: the app passes them in, as copy records or from its
`.resw` (`Loc.Configure("Resources")`, `Loc.UseLanguage(...)` at launch).

## A companion app on Windows

On `Laching.Windows.Companion` a WinUI 3 app is a model, its views and three small files.
[`dotnet/Fixture.App`](https://github.com/vmihalachi/laching/tree/master/dotnet/Fixture.App) is the whole of one, over
the fixture bank, and the
[repository's README](https://github.com/vmihalachi/laching/blob/master/README.md#a-companion-app-on-windows) shows its
`Program.cs` and `App`.

The app keeps its words, its look (a `CompanionPalette` of `ThemedColor`s), Home, onboarding, the tray panel's layout,
its own settings and windows, and the MSIX manifest. Its project imports
[`tools/windows/companion.targets`](https://github.com/vmihalachi/laching/blob/master/tools/windows/companion.targets)
from a `laching/` git submodule: x64 and ARM64, Debug unpackaged and every other configuration an MSIX with Native AOT.
The [tools](https://github.com/vmihalachi/laching/blob/master/tools/README.md) beside it take screenshots, smoke-test
the package and lint the WinUI code that breaks only under Native AOT. Keep the model free of WinUI and its tests run
on any OS.

## More

- [The repository](https://github.com/vmihalachi/laching): the Swift and Kotlin sides, and how the three stay
  identical.
- [Getting started](https://github.com/vmihalachi/laching/blob/master/docs/getting-started.md): a new companion app
  from zero.
- [License](https://github.com/vmihalachi/laching/blob/master/LICENSE): MIT.
