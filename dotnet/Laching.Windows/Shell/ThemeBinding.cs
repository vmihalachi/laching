using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Windows.UI.ViewManagement;

namespace Laching.Windows.Shell;

/// <summary>
/// Repaints code-built elements when their theme, the app's palette or Windows' colors change. Global subscriptions
/// exist only while loaded and hold callbacks weakly, so a window that never raises Unloaded can still be collected.
/// Call from the UI thread; system color changes are dispatched back to it. No companion dependency.
/// </summary>
public static class ThemeBinding
{
    private static readonly ChangeListeners listeners = new();
    private static UISettings? settings;
    private static DispatcherQueue? queue;

    /// <summary>Applies now and on theme/load changes; listens for app and Windows color changes while loaded.</summary>
    public static void Keep(FrameworkElement element, Action apply)
    {
        if (settings is null)
        {
            queue = DispatcherQueue.GetForCurrentThread();
            // Keep UISettings alive: Windows otherwise forgets the event handler.
            settings = new UISettings();
            settings.ColorValuesChanged += (_, _) => queue?.TryEnqueue(Refresh);
        }
        var binding = new ChangeBinding(listeners, apply);
        element.ActualThemeChanged += (_, _) => binding.Apply();
        element.Loaded += (_, _) => binding.Loaded();
        element.Unloaded += (_, _) => binding.Unloaded();
        if (element.IsLoaded)
            binding.Loaded();
        else
            binding.Apply();
    }

    /// <summary>Repaints loaded elements after the app changes its palette. UI thread only.</summary>
    public static void Refresh() => listeners.Raise();
}
