---
name: windows-port
description: Work on a Laching companion app's Windows port (companion.json `windows.*`; C# .NET 10, WinUI 3). Use before changing or reading anything under the Windows folder, porting Swift behavior to C#, fixing failing `dotnet test` conformance replays, adding a Windows window, view, service or string, or building the WinUI app.
---

# Windows port

The Windows app mirrors the Mac app on Laching's .NET packages: `Laching.Core` (scheduler, settings store, store),
`Laching.Companion` (phrases, picker, cadence, quiet rules, settings, coordinator, desktop presence, skins, the rig),
`Laching.Windows` (tray, idle, busy, startup, notifications, single instance, `.resw` lookup), since 0.8 the
companion shell (`Laching.Companion.Shell` and `Laching.Windows.Companion`, below) and, in tests, `Laching.Testing`
(fakes, fixture loaders, the conformance replay bodies, the shell's `FakeHost`). The app's own core (`windows.core`)
holds what's app-specific; its tests (`windows.coreTests`) replay the app's vectors. Platform differences and the
folder map are in the app's `.agents/notes/windows-port.md` (or its parity doc).

- Core and model tests run anywhere with the .NET 10 SDK: `dotnet test <windows.dir>/<windows.coreTests>` and
  `<windows.appTests>`. The WinUI app's XAML compiler needs Windows. On a Mac with the Parallels Windows VM, build it
  there from your working tree, uncommitted changes included: `laching/tools/windows/vm.sh build` (x64 and ARM64;
  `verify.sh` does it too), `vm.sh screenshots <folder>` to look at the windows you changed (the PNGs land in the Mac
  folder), `vm.sh smoke` for the Native AOT package. `vm.sh status` says whether the VM is there, and exits 4 while
  another session's run uses it (a run waits for that one, naming it). Without it (Linux, a cloud session), build on
  Windows or push and run the app's Windows workflow, and say so.
- Formatting: `dotnet format whitespace <windows.dir> --folder` (the app's `.editorconfig`).
- Swift → C#: the same type and member names, PascalCased; durations as `double` seconds with a `Seconds` suffix;
  string-backed ids (`FlavorId`, `SkinId`, `PaintRole`) as record structs; `PartContent.Vector` for Swift's `.shapes`.
  The app's bank and art load from the generated JSON (`PhraseBank.Parse`, `CharacterArt.Parse`), so new phrases and
  art need no port.

## The companion shell (0.8)

The Mac app's `LachingCompanionUI` has a Windows twin in two packages, with the same type and member names, mirrored
by role (Laching's `plans/0.8/README.md`, *The map*):

- `Laching.Companion.Shell` (any OS): `CompanionAppModel<TWindow, TSettings>`, which the app subclasses as on the
  Mac (the tick, channels, Shh and wake, the bubble, previews, the status line, the desktop character, the menus as
  `MenuItem` data), the copy records (`StatusCopy`, `MenuCopy`, `DesktopCopy`, `CompanionAppConfiguration`), speech
  in beats and recorded voices, `DeliveryPerformer`, `SignalHub`, `DesktopLayout`, `SampleEnvironment`. Test the app's
  model with `Laching.Testing`'s `FakeHost`, `ManualTimers` and fakes.
- `Laching.Windows.Companion` (WinUI 3): `CompanionApp` (the app's `Application`; `Program.Main` is one
  `CompanionApp.Start` call), `WindowsCompanionHost<TWindow>` over the app's `CompanionViews` (window specs and
  content, the tray panel, the desktop character), `Speaker`, `ChimePlayer`, `CompanionNotifier`, the system signals,
  and the controls: `CharacterCanvas`, `SpeechBubble`, `StatusPill`, `ActionBar`, `PanelRow`, `Pointer`,
  `WindowInset`, the Settings rows (each over a copy record of the app's words), `SupportWindow`, and the screenshot
  run (`ScreenshotCapture`, `ScreenshotScript`).

What stays in the app: the words (`.resw`, passed in as copy records), the look (`Theme/*.xaml`; the controls paint
from `CompanionLook.Current`, set first in `MakeCompanion`, whose `CompanionPalette` can read the app's theme
dictionaries through `ThemedColor.Resource`), Home, onboarding, the panel's layout, the app's own settings and
windows, `Program.cs`, `App.xaml` (a `CompanionApp` root) and the MSIX manifest. Laching's `dotnet/Fixture.App` is a
whole companion on these packages.

- `{x:Bind}` to `WindowInset`'s static properties (`Content`, `ContentOverActionBar`, `PanelPadding`): a function
  call with a number argument crashes the XAML compiler. Don't name an element `Language`.
- Raster characters ship their layers as `Assets\Characters\<skin>.<part>.scale-200.png` (and `-300`).
- A screenshot run: override `RunScreenshots`, build the model over a `SampleEnvironment` and run a
  `ScreenshotScript`; `Program` passes a null instance key while `ScreenshotCapture.IsEnabled`.

## The app project and its tools (0.8.1)

The `.csproj` imports `laching/tools/windows/companion.targets` last (Laching's `tools/README.md`, *Windows apps*):
x64 and ARM64, Debug unpackaged and every other configuration an MSIX with Native AOT, the voices, sounds and fonts
linked from the Mac's folders.

- **Run**: `dotnet run --project <windows.dir>/<windows.app> -p:Platform=x64` (or ARM64): the Debug app, unpackaged,
  over a mock store when `PackageIdentity.IsPackaged` is false; its settings in `LocalSettingsStore.ForApp`'s JSON file.
- **Shoot**: `laching/tools/windows/screenshots.ps1 [-Only home] [-Language it] [-Configuration Release]` (or the
  app's `windows/scripts/` wrapper). Look at every shot you touched, light and dark; `-Only` runs hidden and fast, with
  the windows inactive (`-Front` for active ones), and `-Configuration Release` shoots the Native AOT build, which is
  where untyped XAML casts fail. After changing text or a window's layout, `laching/tools/windows/check-layout.ps1
  [-Only settings]` shoots every language side by side and names the ones whose text doesn't fit.
- **Package**: `laching/tools/windows/store-release.ps1 -Platforms x64 -NoBundle -AnyVersion` publishes the Store
  package (Native AOT, unsigned), checks it is native (no `runtimeconfig.json`) and, with Developer Mode, installs it
  fresh, starts it and removes it (`smoke-package.ps1`; run that on the built `.msix` with `-Keep` for the hand checks a
  package needs: the startup task, a notification click after the app quit). The release itself: `release-windows`.
- **Lint**: `python3 laching/tools/windows/check-winui.py [files]`; `verify.sh` runs it on the changed app files.
- **Images**: `python3 laching/tools/windows/assets.py` after the Mac icon changes; never edit `Assets/` by hand.
- **The model's tests** (`windows.appTests`, `net10.0`): the model, its copy, the window list, the products and the
  notification actions compile into the tests by `<Compile Include="..\App\…" Link="…" />`, so they use no WinUI type
  and no `Loc`; their words come through `IStrings` (`LocStrings.Shared` in the app, `ReswStrings.Load(<en .resw>)` in
  the tests). A file with a WinUI half keeps one file per Mac file and puts that half under `#if WINDOWS`. Port the
  Mac's `AppModelTests` over `FakeHost`, `ManualTimers` and the fakes (`ManualTimers.RunNext()` fires the oldest
  one-shot; `Advance(seconds)` for a joke's beat).

Traps that cost a day each:

- `App.xaml` next to the `.csproj`, never in a folder: elsewhere it isn't the application's XAML and the app stops at
  launch with a message-less `XamlParseException` (0x802B000A). `check-winui.py` knows.
- Never cast an untyped XAML object (`Resources[…]`, `GetTemplateChild`, `e.NewValue` holding a brush): `XamlCast.As<T>`.
- Choose the language first (`Loc.UseLanguage(ScreenshotCapture.Language ?? Loc.Choose(languages))`), before the
  speaker, the voice library or any copy is made; phrases go through `Localize(key, source)`, never `Get`.
- `PurchaseStore.Changed` is an event, not `PropertyChanged`: raise what depends on it (the Supporter's skin) yourself.
- Name clashes: alias `Microsoft.UI.Windowing.AppWindow` against the app's `AppWindow` enum and
  `Microsoft.UI.Input.Pointer` against `Views.Pointer`; the app's namespace `X.App` clashes with the `App` class, so
  write `Application.Current`.
- In `{l:Loc Key='…'}` a key with an apostrophe is written with a backtick; every class over a XAML type is
  `sealed partial`.
- The tray GUIDs (Debug and Release apart) are tied to the executable's path: never change one.

## Conventions

- User-facing text uses the Mac's catalog keys (`Loc.Get("…")`, `Loc.Plural(key, count, args)`, `{l:Loc Key='…'}` in
  XAML). Strings only Windows uses go in with `l10n.py add`, then the `localization` skill, then
  `l10n.py export-windows`. Never read or edit the generated `.resw` files.
- Colors come from the app's palette resources (`{ThemeResource}`) or its `CompanionPalette`, never hard-coded.
- When Swift behavior covered by vectors changes, regenerate them (`conformance` skill); failing `dotnet test` cases
  tell you what to port.
- **Voices**: link the shared voices folder into the package with `CompanionVoiceFolder` (companion.targets keeps the
  tree the Mac bundles and keeps it out of the resource index), and read it from
  `Path.Combine(AppContext.BaseDirectory, "Voice")`. Never write into it from Windows code or by hand.
- Native AOT: no reflection-based `JsonSerializer` (source-generated contexts only), `x:Bind` over `{Binding}`;
  warnings are errors.
- No third-party code beyond Laching and CommunityToolkit.Mvvm. No `internetClient`, `webcam` or `microphone`
  capability in `Package.appxmanifest`, ever.
