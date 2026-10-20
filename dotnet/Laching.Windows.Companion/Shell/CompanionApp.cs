using Laching.Companion.Shell.App;
using Laching.Windows.Companion.Screenshots;
using Laching.Windows.Companion.Settings;
using Laching.Windows.Shell;
using Laching.Windows.Shell.Localization;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;

namespace Laching.Windows.Companion.Shell;

/// <summary>
/// The app's <see cref="Application"/> (Swift's <c>CompanionAppDelegate</c>): one copy runs (<see cref="Start"/>), and
/// once WinUI has started it makes the app's model and launches it, with Home unless Windows started the app at sign-in
/// (the startup task) or to take a notification click (the notifier takes the click). Opening the app again while it runs
/// brings its windows forward, or Home. It runs on with no window open until the model quits. A screenshot run can take
/// over instead (<see cref="RunScreenshots"/>). Without package identity, <see cref="Start"/> also sets the language
/// picked in Settings again, from the app's settings, before WinUI starts.
/// </summary>
/// <remarks>
/// Not generic (XAML can't name a generic base class): the app subclasses it as <c>App.xaml</c>'s root
/// (<c>&lt;shell:CompanionApp x:Class="Example.App" xmlns:shell="using:Laching.Windows.Companion.Shell"&gt;</c>), defines
/// <c>DISABLE_XAML_GENERATED_MAIN</c>, and hands over its model as an <see cref="ICompanionLaunching"/>. Tests never start
/// it: no Windows test host runs the app's <c>Application</c>, so it has no tests check (the Mac's
/// <c>isRunningTests</c>).
/// <code>
/// public static class Program
/// {
///     [STAThread]
///     private static void Main() =&gt;
///         CompanionApp.Start("example", () =&gt; new App(), LanguageSetting.ForApp("Example", App.Languages));
/// }
///
/// public partial class App : CompanionApp
/// {
///     public App() =&gt; InitializeComponent();
///
///     protected override ICompanionLaunching MakeCompanion()
///     {
///         var host = new WindowsCompanionHost&lt;AppWindow&gt;(Views.Standard, TrayIconId, Loc.Get("Example"));
///         var notifier = new CompanionNotifier(Loc.Get("Example"), [("shh", Loc.Get("Shh for an hour"))]);
///         host.OnQuit = notifier.Dispose;
///         return new AppModel(settings, host, notifier /* … */);
///     }
/// }
/// </code>
/// </remarks>
public abstract class CompanionApp : Application
{
    private ICompanionLaunching? companion;

    protected CompanionApp()
    {
        // A tray app lives on with no window open, until the model quits.
        DispatcherShutdownMode = DispatcherShutdownMode.OnExplicitShutdown;
    }

    /// <summary>
    /// The whole of <c>Main</c>, on its STA thread: claims <paramref name="instanceKey"/> or hands the launch to the copy
    /// already running and returns, sets the language picked in Settings (<paramref name="language"/>), then starts WinUI
    /// with the UI thread's <see cref="SynchronizationContext"/> and makes the app. Returns when the app exits.
    /// </summary>
    /// <param name="instanceKey">Any constant string, one per app; null runs this copy beside any other (a screenshot run).</param>
    /// <param name="makeApp">Makes the app's <see cref="CompanionApp"/>, on the UI thread.</param>
    /// <param name="language">
    /// Where <see cref="LanguageRow"/> keeps the pick without package identity (<see cref="LanguageSetting.ForApp"/>, over
    /// the app's settings folder). Its stored tag, when the app still has it, becomes the override <c>Loc.Choose</c> reads
    /// and the process's preferred UI language, which WinUI's own words follow; both have to be set before WinUI starts.
    /// Ignored with package identity, where Windows keeps the pick (<see cref="LanguageSetting.ForApp"/> gives null
    /// there), and in a screenshot run, which never reads the user's settings. Null without a package: the pick lasts
    /// only until the app quits.
    /// </param>
    public static void Start(string? instanceKey, Func<CompanionApp> makeApp, LanguageSetting? language = null)
    {
        WinRT.ComWrappersSupport.InitializeComWrappers();
        if (instanceKey is not null && !SingleInstance.Claim(instanceKey))
            return;
        if (language is not null && !PackageIdentity.IsPackaged && !ScreenshotCapture.IsEnabled)
            LanguagePick.Process = LanguagePick.Start(language);
        Application.Start(callback =>
        {
            var queue = DispatcherQueue.GetForCurrentThread();
            SynchronizationContext.SetSynchronizationContext(new DispatcherQueueSynchronizationContext(queue));
            _ = makeApp();
        });
    }

    /// <summary>
    /// <i>Required.</i> The app's model, with its host and notifier: made once, at launch, on the UI thread, before the
    /// launch's activation is read (the <see cref="CompanionNotifier"/> registers with Windows as it's made).
    /// </summary>
    protected abstract ICompanionLaunching MakeCompanion();

    /// <summary>
    /// Runs a screenshot session instead of launching, when the app is in one; true when it took over. Called again for
    /// each further language of a run of several (<see cref="ScreenshotCapture.Languages"/>), so it makes its model,
    /// host and views anew each time, in <see cref="ScreenshotCapture.Language"/>.
    /// </summary>
    protected virtual bool RunScreenshots() => false;

    protected sealed override void OnLaunched(LaunchActivatedEventArgs args)
    {
        ScreenshotCapture.RunAgain = () => RunScreenshots();
        if (RunScreenshots())
            return;
        // Made before the activation is read: its notifier registers as it's made, and a launch for a notification click
        // reads the click from that registration.
        var made = MakeCompanion();
        companion = made;
        var instance = AppInstance.GetCurrent();
        // A second launch, redirected here by Start (raised on a background thread). Its kind is read here: a redirected
        // click lives in the copy that handed it over, which exits once this returns.
        var queue = DispatcherQueue.GetForCurrentThread();
        instance.Activated += (_, again) =>
        {
            var kind = again.Kind;
            queue.TryEnqueue(() => Reactivated(kind));
        };
        var arguments = Environment.GetCommandLineArgs();
        made.Launch(showHome: CompanionActivation.IsOpening(arguments, () => instance.GetActivatedEventArgs().Kind));
    }

    private void Reactivated(ExtendedActivationKind kind)
    {
        if (CompanionActivation.IsOpening(kind))
            companion?.Reopen();
    }
}

/// <summary>What an activation means to a companion app.</summary>
internal static class CompanionActivation
{
    /// <summary>
    /// The user opened the app (Start, a shortcut, the executable): Home, or the open windows. Not when the startup task
    /// started it at sign-in (<see cref="StartupService.IsStartupActivation"/>, the Mac's login item), nor when Windows
    /// started it for a notification click, which the notifier takes.
    /// </summary>
    public static bool IsOpening(ExtendedActivationKind kind) =>
        kind is not (ExtendedActivationKind.StartupTask or ExtendedActivationKind.AppNotification);

    /// <summary>
    /// <see cref="IsOpening(ExtendedActivationKind)"/> for the launch: not when <paramref name="arguments"/> (the command
    /// line's) say Windows started the app for a notification click (<see cref="AppNotifier.IsClickLaunch"/>), which it
    /// can report as a plain <see cref="ExtendedActivationKind.Launch"/>. <paramref name="kind"/> is read only for any
    /// other launch: for a click, reading the activation waits for the click to arrive, and throws when the notifier
    /// couldn't register.
    /// </summary>
    public static bool IsOpening(IEnumerable<string> arguments, Func<ExtendedActivationKind> kind) =>
        !AppNotifier.IsClickLaunch(arguments) && IsOpening(kind());
}
