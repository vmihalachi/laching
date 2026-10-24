using Microsoft.UI.Xaml;

namespace Laching.Windows.Shell;

/// <summary>Reads app and explicitly themed resources through Native AOT safe casts; never changes the app's resources.</summary>
public static class XamlResources
{
    /// <summary>A required app resource, preserving WinUI's own merged-dictionary precedence.</summary>
    public static T Get<T>(string key) where T : class =>
        XamlCast.As<T>(Application.Current.Resources[key])
        ?? throw new InvalidCastException($"The resource {key} isn't a {typeof(T).Name}.");

    /// <summary>An optional app resource, preserving WinUI's own merged-dictionary precedence.</summary>
    public static T? Find<T>(string key) where T : class =>
        Application.Current.Resources.TryGetValue(key, out var value) ? XamlCast.As<T>(value) : null;

    /// <summary>
    /// An explicit theme's value (Light, Dark, Default or HighContrast). Later merged dictionaries override earlier
    /// ones; plain resources are considered too. The caller chooses any fallback theme, so contrast stays explicit.
    /// </summary>
    public static object? FindThemed(string key, string theme) => FindThemed(Application.Current.Resources, key, theme);

    private static object? FindThemed(ResourceDictionary resources, string key, string theme)
    {
        // Preserve the palette lookup used by the companion controls, including later ink-plate overrides.
        for (var index = resources.MergedDictionaries.Count - 1; index >= 0; index--)
            if (FindThemed(resources.MergedDictionaries[index], key, theme) is { } merged)
                return merged;
        if (resources.ThemeDictionaries.TryGetValue(theme, out var themed)
            && XamlCast.As<ResourceDictionary>(themed) is { } colors
            && colors.TryGetValue(key, out var value))
            return value;
        return resources.TryGetValue(key, out var plain) ? plain : null;
    }
}
