using Laching.Companion.Settings;
using Laching.Companion.Shell.App;
using Laching.Companion.Shell.Screenshots;
using Laching.Companion.Shell.Signals;
using Laching.Core.Settings;
using Laching.Core.Store;
using Laching.Testing.Fixtures;
using Laching.Windows.Companion.Screenshots;
using Laching.Windows.Companion.Shell;
using Laching.Windows.Companion.Signals;
using Laching.Windows.Companion.Speech;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell.Localization;
using Laching.Windows.System;
using Microsoft.UI.Xaml.Media;
using FixtureApp.Views;
using Shot = Laching.Windows.Companion.Screenshots.ScreenshotScript<Laching.Testing.Fixtures.FixtureWindow, Laching.Companion.Settings.CompanionSettings>.Shot;

namespace FixtureApp;

/// <summary>
/// The fixture app's <c>App.xaml</c>: Laching's <see cref="CompanionApp"/> makes one copy run and launches the model.
/// Settings live in memory, so every launch starts fresh: onboarding done, unless <c>--first-run</c>. Only the language
/// picked in Settings outlives a launch, in a file as an unpackaged app keeps it (<see cref="Program"/>). With
/// <c>LACHING_SCREENSHOT_MODE</c> set (<c>scripts/fixture-screenshots.ps1</c>), it shoots its windows instead.
/// </summary>
public partial class App : CompanionApp
{
    /// <summary>
    /// The languages Settings offers, the default first. The fixture's own words are English in each (it has no
    /// strings); WinUI's words, the dates and the language row follow the pick.
    /// </summary>
    internal static readonly IReadOnlyList<string> Languages = ["en", "de", "ro"];

    /// <summary>The tray icon's identity: Windows keeps its place in the notification area by it.</summary>
    private static readonly Guid TrayIconId = new("5b0f6c1e-3d2a-4f87-9e4b-7a1c2d3e4f50");

    private FixtureModel? made;

    public App()
    {
        InitializeComponent();
    }

    private static string Root => Path.Combine(AppContext.BaseDirectory, "conformance");

    private static ProductCatalog Catalog => new([FixtureModel.Supporter], [FixtureSupport.Tip]);

    protected override ICompanionLaunching MakeCompanion()
    {
        Prepare();
        var host = MakeHost();
        var notifier = new CompanionNotifier("Fixture", [(FixtureModel.SayMore, "Say more"), (FixtureModel.ShhHour, "Shh for an hour")]);
        host.OnQuit = notifier.Dispose;

        var store = new InMemorySettingsStore();
        var settings = new CompanionSettings(store, FixtureModel.Defaults);
        settings.HasCompletedOnboarding = !Environment.GetCommandLineArgs().Contains("--first-run");
        var idle = new SystemIdleTimeProvider();
        var busy = new SystemBusyStateProvider();
        var signals = new SignalHub(
            idle,
            busy,
            new SystemAudioOutput(),
            new SystemScreenLock("Laching.Fixture.SessionLock"),
            new SystemPowerSource(),
            new SystemAppearance(),
            host.Timers
        );
        made = new FixtureModel(
            settings,
            FixtureBank.Load(Root),
            FixtureArt.Load(Root, FixtureIds.VectorSkin.RawValue),
            FixtureArt.Load(Root, FixtureIds.RasterSkin.RawValue),
            host,
            new SkippingClock(),
            idle,
            busy,
            new Speaker("en"),
            new ChimePlayer(FixtureSounds.Write()),
            notifier,
            signals,
            new PurchaseStore(Catalog, new MockStoreBackend(Catalog), store)
        );
        return made;
    }

    /// <summary>
    /// Every window in light and dark over sample state (a fixed Tuesday at 10:30, silent channels, sample prices): Home
    /// with a line in the bubble, Settings, onboarding, Support asked, bought and as a supporter, the panel, and the
    /// desktop character with its bubble.
    /// </summary>
    protected override bool RunScreenshots()
    {
        if (!ScreenshotCapture.IsEnabled)
            return false;
        Prepare();
        var host = MakeHost();
        var sample = new SampleEnvironment(Catalog, new Dictionary<string, string> { [FixtureModel.Supporter] = "€4.99", [FixtureSupport.Tip] = "€1.99" }, host.Timers);
        var settings = new CompanionSettings(sample.Store, FixtureModel.Defaults) { HasCompletedOnboarding = true };
        var model = made = new FixtureModel(
            settings,
            FixtureBank.Load(Root),
            FixtureArt.Load(Root, FixtureIds.VectorSkin.RawValue),
            FixtureArt.Load(Root, FixtureIds.RasterSkin.RawValue),
            host,
            sample.Clock,
            sample.Idle,
            sample.Busy,
            sample.Speaker,
            sample.Chime,
            sample.Notifier,
            sample.Signals,
            sample.Purchases(),
            sample.Seed
        );
        new ScreenshotScript<FixtureWindow, CompanionSettings>(model, host, sample.Backend, id => id.ToString().ToLowerInvariant()).Run(
            () =>
            {
                model.ShowBubble("Fixture line, for the screenshots: long enough to wrap once.");
                return Task.CompletedTask;
            },
            [
                new Shot.Window(FixtureWindow.Home),
                new Shot.Window(FixtureWindow.Settings),
                new Shot.Window(FixtureWindow.Onboarding),
                new Shot.Window(FixtureWindow.Support),
                new Shot.SupporterSequence(FixtureWindow.Support, FixtureModel.Supporter, "support-thanks", "support-supporter"),
                new Shot.Fitting("panel", () => new PanelView(model)),
                new Shot.Fitting("desktop", () => DesktopCharacter.Sheet(model)),
            ]
        );
        return true;
    }

    /// <summary>The language, as the apps choose it, then the look every control paints from.</summary>
    private static void Prepare()
    {
        Loc.UseLanguage(ScreenshotCapture.Language ?? Loc.Choose(Languages));
        // A display face Windows ships, so the bubble's own face shows without an app's font.
        CompanionLook.Current = new CompanionLook(FixtureModel.Palette) { DisplayFont = new FontFamily("Georgia") };
    }

    private WindowsCompanionHost<FixtureWindow> MakeHost()
    {
        FixtureModel Model() => made ?? throw new InvalidOperationException("The views open after the model is made.");
        return new WindowsCompanionHost<FixtureWindow>(
            new CompanionViews<FixtureWindow>(FixtureModel.Spec, id => Content(Model(), id), () => new PanelView(Model()))
            {
                DesktopCharacter = () => DesktopCharacter.Make(Model()),
            },
            TrayIconId,
            FixtureModel.Configuration.StatusItemTooltip
        )
        {
            MenuAccessKeys = true,
        };
    }

    private static Microsoft.UI.Xaml.UIElement Content(FixtureModel model, FixtureWindow id) =>
        id switch
        {
            FixtureWindow.Home => new HomeView(model),
            FixtureWindow.Onboarding => new OnboardingView(model),
            FixtureWindow.Settings => new SettingsView(model),
            FixtureWindow.Support => FixtureSupport.Make(model),
            _ => throw new ArgumentOutOfRangeException(nameof(id), id, null),
        };
}
