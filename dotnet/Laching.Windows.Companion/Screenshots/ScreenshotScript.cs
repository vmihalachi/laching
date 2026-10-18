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
/// A screenshot run over an app's model (Swift's <c>ScreenshotScript</c>): each shot in light, then in dark (or in the
/// contrast theme alone while Windows is in one), saved as <c>&lt;name&gt;-&lt;appearance&gt;.png</c> in
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

        /// <summary>Anything else: the app writes the picture to the path it's given, in the appearance's theme.</summary>
        public sealed record Custom(string Name, Func<string, ElementTheme, Task> Capture) : Shot;
    }

    /// <summary>
    /// The whole run: empties the output folder, loads the sample prices, lets the app <paramref name="prepare"/> (a line
    /// in the bubble), takes <paramref name="shots"/> in each appearance and quits.
    /// </summary>
    public async void Run(Func<Task> prepare, IReadOnlyList<Shot> shots)
    {
        host.Windows.OpensHidden = ScreenshotCapture.IsHidden;
        try
        {
            ScreenshotCapture.PrepareOutputDirectory();
            await model.Purchases.LoadProductsAsync();
            await prepare();
            foreach (var (name, theme) in ScreenshotCapture.Appearances)
            {
                host.ApplyAppearance(
                    theme switch
                    {
                        ElementTheme.Light => AppearanceSetting.Light,
                        ElementTheme.Dark => AppearanceSetting.Dark,
                        _ => AppearanceSetting.System,
                    }
                );
                foreach (var shot in shots)
                    await Take(shot, name, theme);
            }
        }
        catch (Exception e)
        {
            ScreenshotCapture.Report($"the run stopped: {e.GetType().Name} {e.Message}");
        }
        ScreenshotCapture.Finish();
    }

    private async Task Take(Shot shot, string appearance, ElementTheme theme)
    {
        string Path(string name) => IOPath.Combine(ScreenshotCapture.OutputDirectory, $"{name}-{appearance}.png");
        switch (shot)
        {
            case Shot.Window(var id):
                var name = windowName(id);
                if (!ScreenshotCapture.Wants(name))
                    return;
                await Open(id, Path(name));
                host.Windows.Close(id);
                break;
            case Shot.View view:
                if (ScreenshotCapture.Wants(view.Name))
                    await ScreenshotCapture.CaptureWindow(view.Make(), view.Size, theme, Path(view.Name));
                break;
            case Shot.Fitting fitting:
                if (ScreenshotCapture.Wants(fitting.Name))
                    await ScreenshotCapture.CaptureFitting(fitting.Make(), theme, Path(fitting.Name));
                break;
            case Shot.SupporterSequence sequence:
                var owned = backend.Owned.ToHashSet();
                string[] names = owned.Contains(sequence.Product) ? [sequence.Supporter] : [sequence.Thanks, sequence.Supporter];
                if (!names.Any(name => ScreenshotCapture.Wants(name)))
                    return;
                host.Windows.Show(sequence.Id);
                await Drawn(sequence.Id, TimeSpan.FromSeconds(0.6));
                backend.Owned = [.. owned, sequence.Product];
                await model.Purchases.RefreshEntitlementsAsync();
                foreach (var shotName in names)
                {
                    await Drawn(sequence.Id, ScreenshotCapture.Settle);
                    if (ScreenshotCapture.Wants(shotName) && host.Windows.Find(sequence.Id) is { } window)
                        await ScreenshotCapture.Capture(window, Path(shotName));
                    host.Windows.Close(sequence.Id);
                    host.Windows.Show(sequence.Id);
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
                    await custom.Capture(Path(custom.Name), theme);
                break;
        }
    }

    private async Task Open(TWindow id, string path)
    {
        host.Windows.Show(id);
        await Drawn(id, ScreenshotCapture.Settle);
        if (host.Windows.Find(id) is { } window)
            await ScreenshotCapture.Capture(window, path);
        else
            ScreenshotCapture.Report($"{IOPath.GetFileName(path)}: the window didn't open");
    }

    /// <summary>The open window's wait to draw (<see cref="ScreenshotCapture.Drawn(Microsoft.UI.Xaml.Window, TimeSpan)"/>), or <paramref name="wait"/> when it isn't open.</summary>
    private Task Drawn(TWindow id, TimeSpan wait) =>
        host.Windows.Find(id) is { } window ? ScreenshotCapture.Drawn(window, wait) : Task.Delay(wait);
}
