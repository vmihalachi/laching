# Laching

Building blocks for little companion apps that live in the menu bar, the tray or your pocket: the parts every such
app needs and nobody wants to write twice. Swift for macOS and iOS, .NET for Windows, Kotlin for Android. MIT.

Laching grew out of [mybackhurts](https://mybackhurts.app), which ships it, and [sunnysays](https://sunnysays.app), a
desk companion in development whose character became the companion layer. [Mister Pagliacci](https://pagliacci.app), a
clown who tells jokes from the menu bar, is the second companion app built on that layer (in design). It is `0.x` until
the second app has settled the API, so minor versions may change public types.

**No support.** Laching is shared as is, for anyone to use, but it is built for these apps: its
[issues](https://github.com/vmihalachi/laching/issues) are the owner's own plans, issues and pull requests from
others aren't monitored, and releases follow what the apps need.

## What's inside

| Swift product | .NET package | What it does |
|---|---|---|
| `LachingCore` | `Laching.Core` | Pure logic. `ReminderScheduler`: an interval timer with active hours and weekdays, quiet hours, an idle reset ("you were away, the timer restarts"), a hold while the user is busy with a grace period after, snooze, "after my next call" and pause. `Clock`, `IdleTimeProvider` and `BusyStateProvider` protocols so it runs on an injected clock. A seeded `SplitMix64` random source for deterministic jitter. No OS or UI dependencies, so it builds anywhere Swift, .NET or Kotlin runs. |
| `LachingCompanion` | `Laching.Companion` | The pure core of a desk companion: a phrase bank and picker (lines, jokes told in beats, tunes; scoped by skin, flavor and language), chatter cadence presets, quiet rules and mood, write-through settings an app subclasses, a coordinator that replays a whole day from a seed, desktop presence, skins, and a rig that animates vector or raster character art. The app injects its lines, defaults, skins and palette; the .NET side loads them from the JSON the Swift side writes (`PhraseBank.Parse`, `CharacterArt.Parse`). |
| `LachingSystem` | `Laching.Windows` | The real providers. Idle time from the last input event. **Call detection without permissions:** whether another app is using the camera or the microphone, read from device state (CoreAudio's process list on macOS, the capability consent store on Windows), never by opening a device. Full-screen detection. Power source (battery, charging, level). Output muted and route. Screen locked. Appearance changes. |
| `LachingShell` | `Laching.Windows` | Menu bar / tray app plumbing. A status item with a popover panel, a right-click menu and an animated glyph the app draws. Speech: a speaker (the system voice or a recorded clip), a sequencer that tells a joke in beats, a library of recorded voices that knows which clips are current, a chime player. A window manager that shows the Dock icon only while a window is open, a login item toggle, "was I launched at login?", a notification poster with categories and actions, a floating desktop window (macOS), a raw `Shell_NotifyIcon` tray icon host with a native menu, single-instance startup, a `.resw` lookup, drawing helpers (SVG path sampling, keyframes, a hand-drawn wobble). |
| `LachingCompanionUI` | `Laching.Companion.Shell` + `Laching.Windows.Companion` | macOS: the companion on screen, over `LachingCompanion`. `SignalHub` (the snapshot and the events from the system signals), the menu bar glyph, `CharacterView` (vector and image parts, the wake spring), `DesktopCompanion` (the character and its speech bubble on the desktop), `DeliveryPerformer` (bubble, blip, speech in beats, the notification), `CompanionNotifier`, a `CompanionPalette` the app sets, the speech bubble and status pill, and `ScreenshotCapture` for screenshot runs. Since 0.7 also the companion shell an app subclasses: `CompanionAppModel` (the tick, the channels, the status item, the desktop, the windows, previews, mute and undo, the status line), its delegate and menus, the Settings rows and editors, the Support window, the window chrome and a `ScreenshotScript`; each takes its words from the app. Since 0.8 the same shell on Windows, in two packages: `Laching.Companion.Shell` (`net10.0`, tested on any OS) is the logic against small interfaces (the app model, the menus as data, speech in beats and recorded voices, the delivery performer, the signal hub, the desktop layout, a screenshot run's sample environment), and `Laching.Windows.Companion` (WinUI 3) implements them: the app's `Application`, the tray status item with its glyph and panel, the windows, the speaker, chime and notifier, the character on the desktop, and the character canvas, speech bubble, status pill, chrome, Settings rows, Support window and screenshot capture. |
| `LachingStore` | `Laching.Core` (`Store/`) + `Laching.Windows` (`StoreContextBackend`) | A purchase store for one-time unlockables and tips (StoreKit 2 on the Mac, the Microsoft Store on Windows), with the backend behind a protocol so tests use a mock. |
| `LachingTesting` | `Laching.Testing` | Fakes for every protocol, fixtures (a phrase bank, defaults, a vector and a raster skin) and the shared conformance vectors' helpers for the apps' own test targets: Swift generates an app's companion vectors, .NET replays them (each check returns its problems, so neither side needs a particular test framework). The standard picker and quiet case lists, `CompanionVectors` and `ConformanceSuite.companion` build an app's whole suite; `CompanionReplay` replays it. On .NET also the companion shell's fakes (`FakeHost`, `ManualTimers`, `FakeSpeaker`…), for testing an app model without a screen. |
| `tools/` | `tools/` | Not a package: Python tools, shell scripts and agent skills for companion apps, which vendor this repo as a `laching/` git submodule pinned to the release. Localization, phrase sync, recorded voices, character art compiling, sounds, fonts, screenshots, verification (including a check that every Laching pin agrees), the store listing tools (field limits, App Store Connect, Partner Center) and agent hooks, all driven by the app's `companion.json`; and `@laching/landing`, the landing page's plumbing as Vite plugins, published to npm. See [`tools/README.md`](tools/README.md). |

Kotlin, for Android (`kotlin/`, a Gradle build an app includes from the submodule; `tools/README.md`): `laching.core`
mirrors `LachingCore` (the scheduler, the random source, plurals, settings, the store, the drawing math),
`laching.android` is the phone-side plumbing (Play Billing, alarms, notifications, reduce motion, the screenshot run)
and `laching.testing` the fakes. No companion in Kotlin yet.

Web, for small ASP.NET Core and Blazor sites on Azure Container Apps behind Cloudflare (`web/`, six `Laching.Web.*`
NuGet packages, formerly Webling): a PostgreSQL job table, the Cloudflare origin check, security and caching headers,
Turnstile and cache purges, Aspire ingress and deployment steps, Tailwind in `dotnet build` and image uploads. They
share nothing with the rest and no app here uses them; Adjudeca does. See [`web/README.md`](web/README.md).

Everything in `LachingSystem` reads state and never prompts. None of it talks to the network.

## Install

**Swift** (macOS 14+), in `Package.swift` or Xcode's package list, pinned to a tag:

```swift
.package(url: "https://github.com/vmihalachi/laching", exact: "0.20.0")
```

**.NET** (10), from NuGet.org. `Laching.Windows` and `Laching.Windows.Companion` need **Windows 11**: they target
`net10.0-windows10.0.22000.0` with `TargetPlatformMinVersion` 10.0.22000.0, so an app targets that or later (the rest
are `net10.0` and run anywhere):

```sh
dotnet add package Laching.Core
dotnet add package Laching.Companion  # the desk companion layer
dotnet add package Laching.Windows   # Windows 11; the tray, idle and busy services
dotnet add package Laching.Companion.Shell    # a companion app's shell, platform-free (0.8)
dotnet add package Laching.Windows.Companion  # Windows 11, WinUI 3: that shell on Windows (0.8)
dotnet add package Laching.Testing   # in test projects: fakes, fixtures, vector replays
```

## Using it

The package ships no strings, product ids or window lists: the app passes them in.

- **Scheduling.** `ReminderScheduler(config:clock:idle:busy:calendar:random:)`; call `tick()` every 20 s or so and
  act on `.remind`. `SchedulerConfig.intervalJitter` (seconds, default 0) randomizes each interval from the injected
  `RandomSource`; pass `SplitMix64(seed:)` to replay a run.
- **Signals (macOS).** `SystemBusyStateProvider(cameraCheck:)`: `.avFoundation` (default), `.coreMediaIO` for a sandboxed
  app without the camera entitlement, or `.off` (the microphone check alone catches nearly every call).
  `SystemPowerSource`, `SystemAudioOutput`, `AppearanceWatcher` and `ScreenLockWatcher` expose a current value and an
  `onChange` callback.
- **Shell (macOS).** `WindowManager<MyWindow>` takes an enum conforming to `WindowID` (title, size, placement, exclusivity)
  and a content provider; `NotificationPoster(categories:)` (also on iOS) takes action ids with already localized
  titles and reports `onAction(categoryID, actionID)`, or `onAnswer` with the request and its `userInfo`; for an app the
  system ends between reminders, `replacePending(prefix:with:)` schedules `ScheduledNotification`s ahead; `LoginItem` and
  `LaunchContext.launchedAtLogin(appleEvent:)` cover start-at-login. `FloatingWindow(content:size:anchor:)` floats SwiftUI
  content above other windows on every Space (not full-screen ones) without activating the app: click, drag, right-click
  menu and pinch callbacks, fades, a window riding along beside it (a speech bubble), and `FloatingPlacement` for the
  math (kept on a screen when displays change, a side facing the middle, a platform-neutral top-left origin).
- **Store.** `PurchaseStore(catalog: ProductCatalog(unlockables:tips:))`, `@Observable` (hold it in `@State` or your
  model); `owned`, `isOwned(_:)`, `buy`, `tip`, `restore`; `error: PurchaseError?` is for the app to put into words.
  Tests use `MockStoreBackend`. On Windows the same store (`Laching.Core.Store`) runs over
  `StoreContextBackend(dispatcher, windowHandle)`, which matches add-ons by their Partner Center Product ID and reports
  consumables fulfilled so tips can be bought again; `MockStoreBackend` ships in `Laching.Core` there, for tests and an
  app's preview modes.
- **A companion's profile.** `CompanionDefaults(skin:phraseFlavors:)` states the two product choices (the default
  skin, the flavors on); the package's numbers fill the rest. Subclass
  `CompanionSettings(store:defaults:)` (an `@Observable open class` over any `SettingsStore`) to add the app's own
  keys beside the 22 generic ones. `CompanionCoordinator(settings:bank:language:clock:idle:busy:calendar:seed:)` takes
  your `PhraseBank`; call `tick(snapshot:)` every 20 s and deliver what it returns (`DeliveryPerformer` on the Mac).
- **Skins.** `SkinCatalog([SkinEntry(art:unlockedBy:)])`: the first entry is the default, and a skin whose
  `unlockedBy` product isn't owned (or was refunded) draws the default. Art is vector shapes or raster layers
  (`tools/character.py` compiles both to `CharacterArt`); `CharacterView` draws image parts from `artBundle`, colors
  vector parts through the `companionPalette` you set in the environment. A phrase's `skins` limits who can say it.
- **Sequences.** `Phrase.Kind.sequence(source:)` is a joke in beats (two or more parts, catalog keys
  `phrase.<id>.1`, `.2`…), spaced by `PhraseTiming`, spoken by `SpeechSequencer`; it counts as one phrase for recents
  and muting, and the notification shows it once, at the punchline.
- **Languages.** `PhraseContext` takes the app's language: a phrase with `languages` set (`["it"]`) is only picked
  in those, so a joke that only works in Italian is written in Italian. The bank holds source text; the app's String
  Catalog (`phrase.<id>`) supplies the words on screen.
- **Windows.** In `Laching.Windows`: `TrayIconHost`, `AppNotifier<TAction>`, `StartupService(taskId)`, `LocalSettingsStore`,
  `SingleInstance.Claim(key)`, `WavPlayer`, and the signals `PowerSource`, `OutputMute`, `AppearanceWatcher`,
  `SessionLockWatcher`. String lookup is `Loc` (`Loc.Configure("Resources")`, `Loc.UseLanguage(...)` at launch) and
  `Formats.Configure(new FormatKeys(...))` names the six catalog keys the formatters need.

## A companion app on Windows

On `Laching.Windows.Companion` (0.8) a WinUI 3 app is a model, its views and three small files.
[`dotnet/Fixture.App`](dotnet/Fixture.App) is the whole of one, over the fixture bank.

```csharp
// Program.cs: the whole of Main (the csproj defines DISABLE_XAML_GENERATED_MAIN). One copy runs; a second launch
// reopens it. A screenshot run claims no single instance. Unpackaged, the language picked in Settings is kept in the
// app's settings file and set again here (packaged, Windows keeps it and ForApp gives null).
public static class Program
{
    [STAThread]
    private static void Main() =>
        CompanionApp.Start(
            ScreenshotCapture.IsEnabled ? null : "example.myapp",
            () => new App(),
            LanguageSetting.ForApp("MyApp", App.Languages));
}

// App.xaml is a <shell:CompanionApp> (xmlns:shell="using:Laching.Windows.Companion.Shell") with XamlControlsResources.
public partial class App : CompanionApp
{
    internal static readonly IReadOnlyList<string> Languages = ["en", "it"];  // its Strings folders, the default first

    private MyModel? model;

    public App() => InitializeComponent();

    protected override ICompanionLaunching MakeCompanion()
    {
        Loc.UseLanguage(Loc.Choose(Languages));  // the strings and the culture, before anything says a word
        CompanionLook.Current = new CompanionLook(MyPalette.Value);  // then the look: every control paints from it
        var host = new WindowsCompanionHost<MyWindow>(
            new CompanionViews<MyWindow>(MyModel.Spec, id => MyViews.Window(model!, id), () => new MyPanel(model!))
            {
                DesktopCharacter = () => MyViews.Desktop(model!),  // a CharacterCanvas bound to the model
            },
            TrayIconId,
            "My app");
        var settings = new MySettings(LocalSettingsStore.ForApp("MyApp"), MyDefaults.Value);  // packaged or not
        // The speaker, chime, notifier, signal hub and store, as dotnet/Fixture.App/App.xaml.cs makes them.
        return model = new MyModel(settings, MyBank.Load(), host /* , … */);  // a CompanionAppModel<MyWindow, MySettings>
    }

    // With LACHING_SCREENSHOT_MODE set: the same model over a SampleEnvironment (its Seed too, so the next line's time
    // is the same every run), through a ScreenshotScript.
    protected override bool RunScreenshots() => false;
}
```

The app keeps its words (copy records, from its `.resw`), its look (a `CompanionPalette` of `ThemedColor`s, which can
read the app's own theme dictionaries; buttons, cards, titles as `Style`s), Home, onboarding, the tray panel's layout,
its own settings and windows, and the MSIX manifest (the startup task's id goes to `LaunchAtStartupToggle`). Image
parts of a raster character ship as `Assets\Characters\<skin>.<part>.scale-200.png`.

The project imports [`tools/windows/companion.targets`](tools/windows/companion.targets) from the app's `laching/`
submodule, last: x64 and ARM64, Debug unpackaged (`dotnet run`; `PackageIdentity.IsPackaged` is false, so the app
picks a mock store, and `LocalSettingsStore.ForApp` keeps a JSON file) and every other configuration an MSIX with Native
AOT, the voice clips, sounds and fonts linked from the folders the Mac app bundles.
Beside it, `tools/windows/` has the screenshot runner, a package smoke test, a lint for the WinUI code that breaks only
under Native AOT, and the MSIX images from the app icon ([`tools/README.md`](tools/README.md), *Windows apps*). Keep the
model free of WinUI and its words behind `IStrings`, and its tests run on any OS (`Laching.Testing`'s `ReswStrings`
reads the app's English `.resw`).

## The implementations stay identical

The Swift code is the reference. `conformance/` holds JSON vectors generated from it (`scheduler/`, and `companion/`
over the fixture bank and skins), and `dotnet/Laching.Core.Tests` and `dotnet/Laching.Companion.Tests` replay every
one (`kotlin/core` the scheduler's), so a change in behavior on one side fails the other sides' tests. Apps generate their own content vectors with
`LachingTesting` and replay them through `Laching.Testing`. See `conformance/README.md`.

## Develop

```sh
swift build && swift test                       # macOS: the package and its tests
scripts/conformance.sh                          # regenerate conformance/ from the Swift code
cd dotnet && dotnet test Laching.Core.Tests    # .NET: Core builds and tests anywhere; Laching.Windows builds on Windows (its PRI tooling)
cd dotnet && dotnet test Laching.Companion.Tests   # the companion vectors, anywhere
cd dotnet && dotnet test Laching.Companion.Shell.Tests   # the companion shell's logic, anywhere
cd dotnet && dotnet test Laching.Testing.Tests     # the mocks, conformance helpers and fixtures, anywhere
cd dotnet && dotnet test Laching.Windows.Companion.Tests # Windows: the shell's Windows half
dotnet run --project dotnet/Fixture.App         # Windows: the fixture app, a companion on the fixture bank
scripts/fixture-screenshots.ps1                 # Windows: its windows to PNGs, light and dark (-Only home: hidden, a few seconds)
python3 tools/tests/run.py                      # the tools' tests
dotnet test web/Laching.Web.sln                 # the web packages (the Jobs tests need PostgreSQL: web/README.md)
scripts/verify.sh                               # the checks that apply to what you changed
```

Formatting: `xcrun swift-format format -i --configuration .swift-format <files>` and `dotnet format whitespace dotnet --folder`.
Coding agents: read `AGENTS.md`.

## Releases

Releases are made on demand: Actions → Release → Run workflow (`release.yml`, on master), with `dry_run` first to see
the plan. The number comes from the pull requests merged since the last tag: a patch, or a minor while 0.x (a major
from 1.0) when one is labelled `breaking`; the `bump` input can ask for another. Once what ships has been tested and
packed, the run commits the bump to master as `X.Y.Z: release` and tags it. One tag per release: Swift and Kotlin take the tag; NuGet and npm get a new version only
when their code changed since their last one. When the .NET packages changed since `LachingVersion`
(`dotnet/Directory.Build.props`), the run packs all six at X.Y.Z and pushes them to NuGet.org through
[Trusted Publishing](https://learn.microsoft.com/nuget/nuget-org/trusted-publishing) (no stored key: a policy on
nuget.org for repository `vmihalachi/laching` and workflow `release.yml`, plus the `NUGET_USER` repository variable
holding the nuget.org username; without them it says so and attaches the packages to the release instead). The six
`Laching.Web.*` packages work the same way on their own version, `LachingWebVersion` (`web/Directory.Build.props`). It
publishes `@laching/landing` to npm when it changed, through trusted publishing as well
([`tools/landing/README.md`](tools/landing/README.md)), and makes the GitHub release, its notes generated from the
merged pull requests' titles. The latest release is `v0.20.0`.

## License

[MIT](LICENSE). © 2026 Vlad Mihalachi.
