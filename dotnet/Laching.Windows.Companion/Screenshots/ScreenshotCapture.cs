using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using Laching.Companion.Phrases;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics;
using Win32 = Laching.Windows.Shell.NativeMethods;
using XamlSize = Windows.Foundation.Size;

namespace Laching.Windows.Companion.Screenshots;

/// <summary>
/// Renders an app's windows and views to PNGs in its own process (Swift's <c>ScreenshotCapture</c>), for documentation
/// and store screenshots: the generic half of a screenshot mode. The app's run script sets <see cref="ModeVariable"/> (a
/// comma-separated list of shot name prefixes, or <c>all</c>) and optionally <see cref="DirectoryVariable"/> (else a
/// folder in the temporary directory), in the environment or, for a packaged app started in its package (which passes no
/// environment), as <c>NAME=value</c> arguments (<see cref="Setting"/>); <see cref="Finish"/> prints the folder last. The app builds its model over a
/// <c>SampleEnvironment</c> (in-memory settings, a fixed clock, silent channels, fixed prices), so the user's settings,
/// speakers and the store are never touched, then shoots what it wants (<see cref="ScreenshotScript{TWindow, TSettings}"/>).
/// While a run goes, the character holds its still pose and nothing animates.
/// </summary>
/// <remarks>
/// A window is captured as the screen shows it (<c>PrintWindow</c> with full content, cropped to its visible frame),
/// caption buttons included, as the Mac's shots show the title bar. Not <c>RenderTargetBitmap</c>: it draws XAML only,
/// so it would miss the composition visuals the character's parts move on and the bubble's shadow and dial. Every shot is
/// also checked: a window that didn't open, a PNG that wasn't written, a window captured before it drew (one flat colour
/// under the caption buttons, still so when taken again), or text that doesn't fit (<see cref="LayoutCheck.TextProblems"/>)
/// is a problem, written to <see cref="ProblemsFile"/> next to the shots. A hidden run (<see cref="IsHidden"/>) cloaks its
/// windows: the compositor still draws them, so <c>PrintWindow</c> captures them as it would on screen, but inactive.
/// </remarks>
public static class ScreenshotCapture
{
    public const string ModeVariable = "LACHING_SCREENSHOT_MODE";
    public const string DirectoryVariable = "LACHING_SCREENSHOT_DIR";

    /// <summary>The file <see cref="Finish"/> writes the run's problems to, next to the shots; absent when there were none.</summary>
    public const string ProblemsFile = "problems.txt";

    /// <summary>How long a window gets to lay out and draw before it is captured, in a run in front.</summary>
    public static readonly TimeSpan Settle = TimeSpan.FromSeconds(1.2);

    /// <summary>How long a hidden run's window goes without a layout pass, its images decoded, before it is captured.</summary>
    private static readonly TimeSpan Quiet = TimeSpan.FromSeconds(0.15);

    /// <summary>
    /// A hidden run's longest wait for a window to draw: longer than <see cref="Settle"/>, since it only runs out when
    /// something keeps laying out, and runs side by side (the layout check) start their windows slower.
    /// </summary>
    private static readonly TimeSpan HiddenLimit = TimeSpan.FromSeconds(5);

    /// <summary>The longest wait for two of XAML's frames, and then for the compositor's present, before a hidden run's capture (<see cref="Presented"/>).</summary>
    private static readonly TimeSpan FrameLimit = TimeSpan.FromSeconds(0.5);

    /// <summary>
    /// What the blank check (<see cref="IsBlank"/>) leaves out at the sides and the bottom of a capture, in DIPs: the
    /// frame's border and Windows 11's rounded corners, which Windows draws whether or not the content has.
    /// </summary>
    private const double BlankInset = 8;

    private static readonly List<string> Found = [];

    /// <summary>The language a run shoots in, an app's tag ("it"); unset runs in the language the app would choose.</summary>
    public const string LanguageVariable = "LACHING_SCREENSHOT_LANGUAGE";

    /// <summary>
    /// <c>1</c>: the run opens its windows hidden (<see cref="Shell.WindowManager{TWindow}.OpensHidden"/>), drawn and
    /// captured but never on screen and never taking the focus, so it leaves the PC to whoever is using it, and shoots each
    /// once it has drawn instead of after <see cref="Settle"/>; the shots show the windows inactive. The scripts set it
    /// for <c>-Only</c> and the layout check.
    /// </summary>
    public const string HiddenVariable = "LACHING_SCREENSHOT_HIDDEN";

    /// <summary>
    /// The skin the run wears (a <see cref="SkinId"/> raw value: <c>auguste</c>), so each costume gets its own set of
    /// shots; unset, the app's default skin. The app wears it before its shots, owning in the sample store whatever
    /// unlocks it. screenshots.ps1 <c>-Skin</c> sets it.
    /// </summary>
    public const string SkinVariable = "LACHING_SCREENSHOT_SKIN";

    public static bool IsEnabled => Setting(ModeVariable) is not null;

    /// <summary>
    /// A run setting: the environment variable <paramref name="name"/>, else a <c>name=value</c> command-line argument,
    /// which is how screenshots.ps1 passes them to a packaged app (<c>Invoke-CommandInDesktopPackage</c> passes no
    /// environment).
    /// </summary>
    public static string? Setting(string name)
    {
        if (Environment.GetEnvironmentVariable(name) is { } value)
            return value;
        var prefix = name + "=";
        return Environment.GetCommandLineArgs().Skip(1).FirstOrDefault(a => a.StartsWith(prefix, StringComparison.Ordinal))?[prefix.Length..];
    }

    public static bool IsHidden => Setting(HiddenVariable) == "1";

    /// <summary>
    /// The language asked for (<see cref="LanguageVariable"/>), else null. An app passes it to <c>Loc.UseLanguage</c>
    /// instead of <c>Loc.Choose</c>'s pick, so one machine shoots every language without changing its own.
    /// </summary>
    public static string? Language =>
        IsEnabled && Setting(LanguageVariable) is { Length: > 0 } language ? language : null;

    /// <summary>The skin asked for (<see cref="SkinVariable"/>), else null.</summary>
    public static SkinId? Skin => IsEnabled && Setting(SkinVariable) is { Length: > 0 } skin ? new SkinId(skin) : (SkinId?)null;

    /// <summary>The shot name prefixes asked for; empty means all.</summary>
    public static IReadOnlyList<string> Wanted => WantedIn(Setting(ModeVariable));

    /// <summary>Whether <paramref name="shot"/> was asked for (by a prefix of its name).</summary>
    public static bool Wants(string shot) => Wants(shot, Setting(ModeVariable));

    /// <summary>Whether <paramref name="mode"/> (the variable's value: prefixes, or <c>all</c>) asks for <paramref name="shot"/>.</summary>
    public static bool Wants(string shot, string? mode) =>
        WantedIn(mode) is var wanted && (wanted.Count == 0 || wanted.Any(p => shot.StartsWith(p, StringComparison.Ordinal)));

    private static IReadOnlyList<string> WantedIn(string? mode) =>
        [.. (mode ?? "").Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries).Where(p => p != "all")];

    /// <summary>
    /// <see cref="DirectoryVariable"/>, else a folder per process in the temporary directory (<c>screenshots-&lt;pid&gt;</c>),
    /// so runs in several languages at once (the layout check) don't share it.
    /// </summary>
    public static string OutputDirectory =>
        Setting(DirectoryVariable) is { Length: > 0 } dir
            ? dir
            : Path.Combine(Path.GetTempPath(), $"screenshots-{Environment.ProcessId}");

    /// <summary>What the run found wrong so far, one line each, starting with the shot's file name.</summary>
    public static IReadOnlyList<string> Problems => Found;

    /// <summary>
    /// The appearances a run shoots: light and dark, or the contrast theme alone while Windows is in one (an app can't
    /// turn a contrast theme on or off for itself, so run it again with one on for those shots).
    /// </summary>
    public static IReadOnlyList<(string Name, ElementTheme Theme)> Appearances =>
        CompanionPalette.ContrastThemeIsOn ? [("contrast", ElementTheme.Default)] : [("light", ElementTheme.Light), ("dark", ElementTheme.Dark)];

    /// <summary>Records a problem (once), for example a window the app expected to open and didn't, and prints it to standard error.</summary>
    public static void Report(string problem)
    {
        if (Found.Contains(problem))
            return;
        Found.Add(problem);
        Console.Error.WriteLine(problem);
    }

    /// <summary>Empties and makes <see cref="OutputDirectory"/>.</summary>
    public static void PrepareOutputDirectory()
    {
        try
        {
            if (Directory.Exists(OutputDirectory))
                Directory.Delete(OutputDirectory, recursive: true);
        }
        catch (IOException) { }
        Directory.CreateDirectory(OutputDirectory);
    }

    /// <summary>Writes the problems found (<see cref="ProblemsFile"/>), prints the output folder (the run script reads the last line) and quits.</summary>
    public static void Finish()
    {
        if (Found.Count > 0)
            File.WriteAllLines(Path.Combine(OutputDirectory, ProblemsFile), Found);
        Console.WriteLine(OutputDirectory);
        Application.Current.Exit();
    }

    /// <summary>
    /// Captures <paramref name="window"/> as the screen shows it, then checks that its text fits. A capture taken before
    /// the window drew (<see cref="IsBlank"/>) is taken again after <see cref="Settle"/> and a presented frame, and
    /// reported when it is still blank.
    /// </summary>
    public static async Task Capture(Window window, string path)
    {
        var shot = Path.GetFileName(path);
        var hwnd = Win32Interop.GetWindowFromWindowId(window.AppWindow.Id);
        // The caption buttons' band, which Windows draws before the content (WindowFrame's); a fitting view has none.
        var band = window.ExtendsContentIntoTitleBar ? WindowInset.TitleBar : 0;
        var drawn = Write(hwnd, path, band, out var blank);
        if (drawn && blank)
        {
            await Task.Delay(Settle);
            await Presented();
            drawn = Write(hwnd, path, band, out blank);
        }
        if (!drawn)
            Report($"{shot}: the window couldn't be drawn");
        else if (blank)
            Report($"{shot}: captured before it drew");
        if (window.Content is UIElement root)
            foreach (var problem in LayoutCheck.TextProblems(root))
                Report($"{shot}: {problem}");
    }

    /// <summary>
    /// <paramref name="view"/> in a window built the way the app's are (<see cref="Shell.WindowFrame"/>: the view under the
    /// caption buttons' band on the look's paper),
    /// <paramref name="size"/> DIPs of content, in <paramref name="theme"/>: for states a real window can't be told to open in.
    /// </summary>
    public static async Task CaptureWindow(UIElement view, XamlSize size, ElementTheme theme, string path)
    {
        var root = Shell.WindowFrame.Make(view);
        root.RequestedTheme = theme;
        var window = new Window { Content = root, ExtendsContentIntoTitleBar = true };
        window.AppWindow.TitleBar.PreferredTheme = theme == ElementTheme.Dark ? TitleBarTheme.Dark : TitleBarTheme.Light;
        var scale = Shell.NativeMethods.Scale(Win32Interop.GetWindowFromWindowId(window.AppWindow.Id));
        // Not ResizeClient, which adds a caption's height (3.3's outcome).
        Shell.NativeMethods.SetClientSize(window.AppWindow, new SizeInt32((int)Math.Round(size.Width * scale), (int)Math.Round(size.Height * scale)));
        Present(window);
        await Drawn(window);
        await Capture(window, path);
        window.Close();
    }

    /// <summary><paramref name="view"/> on its own at its fitting size (the panel, the desktop character), on the look's paper, in <paramref name="theme"/>.</summary>
    public static async Task CaptureFitting(FrameworkElement view, ElementTheme theme, string path)
    {
        var root = new Grid { RequestedTheme = theme, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top };
        LookBinding.Keep(root, () => root.Background = LookBinding.Brush(root, p => p.Paper));
        root.Children.Add(view);
        var window = new Window { Content = root };
        // The context-menu presenter, as the desktop window's: a popup with no frame, so no border edges the shot.
        var presenter = OverlappedPresenter.CreateForContextMenu();
        presenter.SetBorderAndTitleBar(false, false);
        window.AppWindow.SetPresenter(presenter);
        var hwnd = Win32Interop.GetWindowFromWindowId(window.AppWindow.Id);
        Shell.NativeMethods.SquareCorners(hwnd);
        var scale = Shell.NativeMethods.Scale(hwnd);
        void Fit()
        {
            root.Measure(new XamlSize(double.PositiveInfinity, double.PositiveInfinity));
            var fit = root.DesiredSize;
            Shell.NativeMethods.SetClientSize(window.AppWindow, new SizeInt32((int)Math.Ceiling(fit.Width * scale), (int)Math.Ceiling(fit.Height * scale)));
        }
        Fit();
        Present(window);
        await Drawn(window);
        // What x:Bind sets arrives once the view has loaded, so it can be taller now than when it was first measured.
        Fit();
        await Drawn(window, TimeSpan.FromSeconds(0.3));
        await Capture(window, path);
        window.Close();
    }

    /// <summary>Waits for <paramref name="window"/> to lay out and draw before a capture (<see cref="Drawn(Window, TimeSpan)"/> over <see cref="Settle"/>).</summary>
    internal static Task Drawn(Window window) => Drawn(window, Settle);

    /// <summary>
    /// Waits for <paramref name="window"/> to lay out and draw: <paramref name="wait"/> in a run in front; in a hidden run
    /// (<see cref="IsHidden"/>) until its content has loaded, its images have decoded and no layout pass has run for
    /// <see cref="Quiet"/> (a fifth of a second to half a second for most windows), <see cref="HiddenLimit"/> at most, and
    /// then for a frame of that on screen (<see cref="Presented"/>).
    /// </summary>
    internal static async Task Drawn(Window window, TimeSpan wait)
    {
        if (!IsHidden || XamlCast.As<FrameworkElement>(window.Content) is not { } root)
        {
            await Task.Delay(wait);
            return;
        }
        var started = Stopwatch.GetTimestamp();
        var laidOut = started;
        void LaidOut(object? sender, object args) => laidOut = Stopwatch.GetTimestamp();
        root.LayoutUpdated += LaidOut;
        try
        {
            while (Stopwatch.GetElapsedTime(started) < HiddenLimit)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(16));
                if (root.IsLoaded && Stopwatch.GetElapsedTime(laidOut) >= Quiet && !Decoding(root))
                    break;
            }
        }
        finally
        {
            root.LayoutUpdated -= LaidOut;
        }
        await Presented();
    }

    /// <summary>
    /// Waits for the screen to show what the windows have laid out: two of XAML's frames (<c>CompositionTarget.Rendering</c>,
    /// which starts each one: what was laid out and not yet drawn is drawn and committed in the first, so the second
    /// starts once that is done), then the compositor's present of them (<c>DwmFlush</c>, off the UI thread),
    /// <see cref="FrameLimit"/> at most for each. Not <c>Rendered</c>, which comes only for a frame that had something to
    /// draw. A new window's first frame can come after its layout has settled, and a capture before it shows the bare
    /// window: the caption buttons on white or black.
    /// </summary>
    private static async Task Presented()
    {
        var frames = 0;
        var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        void Rendering(object? sender, object args)
        {
            if (++frames == 2)
                started.TrySetResult();
        }
        CompositionTarget.Rendering += Rendering;
        try
        {
            await Task.WhenAny(started.Task, Task.Delay(FrameLimit));
        }
        finally
        {
            CompositionTarget.Rendering -= Rendering;
        }
        await Task.WhenAny(Task.Run(Shell.NativeMethods.DwmFlush), Task.Delay(FrameLimit));
    }

    /// <summary>
    /// Whether a capture shows a window that hadn't drawn yet: one flat colour (the bare window's white or black) under
    /// the caption buttons. <paramref name="pixels"/> holds the capture's rows from the top, <paramref name="width"/>
    /// 32-bit pixels each, whose alpha is ignored. Left out: the top <paramref name="band"/> rows (the caption buttons,
    /// which Windows draws whether or not the content has) and then <paramref name="inset"/> pixels at every edge of
    /// the rest (the frame's border and rounded corners). Not blank when that leaves nothing to look at.
    /// </summary>
    internal static bool IsBlank(ReadOnlySpan<int> pixels, int width, int band, int inset)
    {
        var height = width > 0 ? pixels.Length / width : 0;
        var (top, bottom, left, right) = (band + inset, height - inset, inset, width - inset);
        if (top >= bottom || left >= right)
            return false;
        var colour = pixels[top * width + left] & 0xFFFFFF;
        for (var y = top; y < bottom; y++)
            foreach (var pixel in pixels.Slice(y * width + left, right - left))
                if ((pixel & 0xFFFFFF) != colour)
                    return false;
        return true;
    }

    /// <summary>Whether an image under <paramref name="node"/> is still loading its bitmap (no pixels yet).</summary>
    private static bool Decoding(DependencyObject node)
    {
        if (XamlCast.As<Microsoft.UI.Xaml.Controls.Image>(node) is { Source: { } source } && XamlCast.As<BitmapImage>(source) is { PixelWidth: 0, UriSource: not null })
            return true;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
            if (Decoding(VisualTreeHelper.GetChild(node, i)))
                return true;
        return false;
    }

    /// <summary>Shows a window made for a shot: in front, or hidden (<see cref="IsHidden"/>) as the window manager's are.</summary>
    private static void Present(Window window)
    {
        if (IsHidden)
            Shell.NativeMethods.ShowHidden(window.AppWindow);
        else
            window.Activate();
    }

    /// <summary>
    /// The window's pixels as the screen composes them, cropped to its visible frame (no resize border), as a PNG; false
    /// when they can't be read. <paramref name="blank"/>: whether they show the window before it drew (<see cref="IsBlank"/>,
    /// under a caption band of <paramref name="band"/> DIPs).
    /// </summary>
    private static bool Write(nint hwnd, string path, double band, out bool blank)
    {
        blank = false;
        if (hwnd == 0 || !Win32.GetWindowRect(hwnd, out var outer))
            return false;
        var visible = Shell.NativeMethods.VisibleFrame(hwnd, outer);
        var (width, height) = (outer.Right - outer.Left, outer.Bottom - outer.Top);
        if (width <= 0 || height <= 0)
            return false;
        try
        {
            using var full = new Bitmap(width, height, PixelFormat.Format32bppArgb);
            using (var graphics = Graphics.FromImage(full))
            {
                var hdc = graphics.GetHdc();
                try
                {
                    if (!Shell.NativeMethods.PrintWindow(hwnd, hdc, Shell.NativeMethods.PW_RENDERFULLCONTENT))
                        return false;
                }
                finally
                {
                    graphics.ReleaseHdc(hdc);
                }
            }
            var crop = new Rectangle(visible.Left - outer.Left, visible.Top - outer.Top, visible.Right - visible.Left, visible.Bottom - visible.Top);
            crop.Intersect(new Rectangle(0, 0, width, height));
            using var image = full.Clone(crop, PixelFormat.Format32bppArgb);
            var scale = Shell.NativeMethods.Scale(hwnd);
            blank = IsBlank(Pixels(image), image.Width, (int)Math.Ceiling(band * scale), (int)Math.Ceiling(BlankInset * scale));
            image.Save(path, ImageFormat.Png);
            return true;
        }
        catch (Exception e) when (e is ExternalException or IOException or UnauthorizedAccessException or ArgumentException)
        {
            Report($"{Path.GetFileName(path)}: not written ({e.Message})");
            blank = false;
            return true;
        }
    }

    /// <summary>The image's pixels, row by row from the top, as 32-bit ARGB.</summary>
    private static int[] Pixels(Bitmap image)
    {
        var data = image.LockBits(new Rectangle(0, 0, image.Width, image.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            var pixels = new int[image.Width * image.Height];
            for (var y = 0; y < image.Height; y++)
                Marshal.Copy(data.Scan0 + y * data.Stride, pixels, y * image.Width, image.Width);
            return pixels;
        }
        finally
        {
            image.UnlockBits(data);
        }
    }
}
