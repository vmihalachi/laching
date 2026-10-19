using Laching.Companion.Settings;
using Laching.Companion.Shell.App;
using Laching.Companion.Shell.Screenshots;
using Laching.Core.Store;
using Laching.Windows.Companion.Shell;
using Microsoft.UI.Xaml;
using IOPath = System.IO.Path;
using XamlSize = Windows.Foundation.Size;

namespace Laching.Windows.Companion.Screenshots;

/// <summary>
/// A screenshot run over an app's model (Swift's <c>ScreenshotScript</c>): each shot in light, then in dark
/// (<see cref="ScreenshotCapture.Themes"/>; or in the contrast theme alone while Windows is in one), from one opening of
/// its window, saved as <c>&lt;name&gt;-&lt;appearance&gt;.png</c> in
/// <see cref="ScreenshotCapture.OutputDirectory"/>, skipping the ones <see cref="ScreenshotCapture.Wants"/> doesn't ask
/// for. The app gives the shots in order: its windows, views at a size, views at their fitting size, the supporter
/// sequence, its own. Quits when done. The model is never launched: no tray icon or desktop character.
/// </summary>
public sealed class ScreenshotScript<TWindow, TSettings>(
    CompanionAppModel<TWindow, TSettings> model,
    WindowsCompanionHost<TWindow> host,
    MockStoreBackend backend,
    Func<TWindow, string> windowName
)
    where TWindow : notnull
    where TSettings : CompanionSettings
{
    /// <summary>One picture per appearance.</summary>
    public abstract record Shot
    {
        private Shot() { }

        /// <summary>The real window, opened by its id and named by <c>windowName</c>.</summary>
        public sealed record Window(TWindow Id) : Shot;

        /// <summary>A view in a window built like the app's at <paramref name="Size"/> (DIPs of content), for a state the real window can't be told to open in.</summary>
        public sealed record View(string Name, Func<UIElement> Make, XamlSize Size) : Shot;

        /// <summary>A view on its own at its fitting size (the panel, the desktop character).</summary>
        public sealed record Fitting(string Name, Func<FrameworkElement> Make) : Shot;

        /// <summary>
        /// The Support window bought while open (<paramref name="Thanks"/>: only the thank-you), then opened again
        /// (<paramref name="Supporter"/>: the tips); ownership is back to what it was afterwards. A run that already owns
        /// <paramref name="Product"/> (it wears a skin the product unlocks) can't buy it again, so it shoots only
        /// <paramref name="Supporter"/>.
        /// </summary>
        public sealed record SupporterSequence(TWindow Id, string Product, string Thanks, string Supporter) : Shot;

        /// <summary>Anything else: the app writes the picture to the path it's given, in the theme the app is in.</summary>
        public sealed record Custom(string Name, Func<string, ElementTheme, Task> Capture) : Shot;
    }

    /// <summary>
    /// The whole run: empties the output folder, loads the sample prices, lets the app <paramref name="prepare"/> (a line
    /// in the bubble), takes <paramref name="shots"/>, each in every appearance from one opening of its window, and quits.
    /// </summary>
    public async void Run(Func<Task> prepare, IReadOnlyList<Shot> shots)
    {
        host.Windows.OpensHidden = ScreenshotCapture.IsHidden;
        try
        {
            ScreenshotCapture.PrepareOutputDirectory();
            await model.Purchases.LoadProductsAsync();
            await prepare();
            foreach (var shot in shots)
                await Take(shot);
        }
        catch (Exception e)
        {
            ScreenshotCapture.Report($"the run stopped: {e.GetType().Name} {e.Message}");
        }
        ScreenshotCapture.Finish();
    }

    /// <summary>Switches the app, its open windows included, to <paramref name="theme"/> (<see cref="WindowsCompanionHost{TWindow}.ApplyAppearance"/>).</summary>
    private void Apply(ElementTheme theme) =>
        host.ApplyAppearance(
            theme switch
            {
                ElementTheme.Light => AppearanceSetting.Light,
                ElementTheme.Dark => AppearanceSetting.Dark,
                _ => AppearanceSetting.System,
            }
        );

    private async Task Take(Shot shot)
    {
        static IReadOnlyList<(ElementTheme Theme, string Path)> Paths(string name) =>
            [
                .. ScreenshotCapture.Appearances.Select(appearance =>
                    (appearance.Theme, IOPath.Combine(ScreenshotCapture.OutputDirectory, $"{name}-{appearance.Name}.png"))
                ),
            ];
        switch (shot)
        {
            case Shot.Window(var id):
                var name = windowName(id);
                if (!ScreenshotCapture.Wants(name))
                    return;
                host.Windows.Show(id);
                if (host.Windows.Find(id) is { } window)
                    await Capture(window, id, Paths(name));
                else
                    foreach (var (_, path) in Paths(name))
                        ScreenshotCapture.Report($"{IOPath.GetFileName(path)}: the window didn't open");
                host.Windows.Close(id);
                break;
            case Shot.View view:
                if (ScreenshotCapture.Wants(view.Name))
                    await ScreenshotCapture.CaptureWindow(view.Make(), view.Size, Paths(view.Name));
                break;
            case Shot.Fitting fitting:
                if (ScreenshotCapture.Wants(fitting.Name))
                    await ScreenshotCapture.CaptureFitting(fitting.Make(), Paths(fitting.Name));
                break;
            case Shot.SupporterSequence sequence:
                var owned = backend.Owned.ToHashSet();
                string[] names = owned.Contains(sequence.Product) ? [sequence.Supporter] : [sequence.Thanks, sequence.Supporter];
                if (!names.Any(name => ScreenshotCapture.Wants(name)))
                    return;
                host.Windows.Show(sequence.Id);
                await Drawn(sequence.Id, TimeSpan.FromSeconds(0.6)); // open before the purchase
                backend.Owned = [.. owned, sequence.Product];
                await model.Purchases.RefreshEntitlementsAsync();
                for (var index = 0; index < names.Length; index++)
                {
                    if (index > 0) // opened again, as a supporter
                    {
                        host.Windows.Close(sequence.Id);
                        host.Windows.Show(sequence.Id);
                    }
                    if (ScreenshotCapture.Wants(names[index]) && host.Windows.Find(sequence.Id) is { } open)
                        await Capture(open, sequence.Id, Paths(names[index]));
                }
                host.Windows.Close(sequence.Id);
                if (!backend.Owned.SetEquals(owned))
                {
                    backend.Owned = owned;
                    await model.Purchases.RefreshEntitlementsAsync();
                }
                break;
            case Shot.Custom custom:
                if (ScreenshotCapture.Wants(custom.Name))
                    foreach (var (theme, path) in Paths(custom.Name))
                    {
                        Apply(theme);
                        await custom.Capture(path, theme);
                    }
                break;
        }
    }

    /// <summary>The open <paramref name="window"/> in each theme of <paramref name="shots"/>: the app switched to it, the window drawn again, captured.</summary>
    private async Task Capture(Microsoft.UI.Xaml.Window window, TWindow id, IReadOnlyList<(ElementTheme Theme, string Path)> shots)
    {
        foreach (var (theme, path) in shots)
        {
            Apply(theme);
            await Drawn(id, ScreenshotCapture.Settle);
            await ScreenshotCapture.Capture(window, path);
        }
    }

    /// <summary>The open window's wait to draw (<see cref="ScreenshotCapture.Drawn(Microsoft.UI.Xaml.Window, TimeSpan)"/>), or <paramref name="wait"/> when it isn't open.</summary>
    private Task Drawn(TWindow id, TimeSpan wait) =>
        host.Windows.Find(id) is { } window ? ScreenshotCapture.Drawn(window, wait) : Task.Delay(wait);
}
