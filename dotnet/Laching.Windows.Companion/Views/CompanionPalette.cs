using Laching.Companion.Figures;
using Laching.Windows.Shell;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI;
using Windows.UI.ViewManagement;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// A color in each appearance (Swift's <c>CompanionPalette.dynamic(light:dark:)</c>): the controls pick one by their
/// <see cref="FrameworkElement.ActualTheme"/> and pick again when it changes.
/// </summary>
public readonly record struct ThemedColor(Color Light, Color Dark)
{
    public Color For(ElementTheme theme) => theme == ElementTheme.Dark ? Dark : Light;

    /// <summary>The same color in light and dark, for paints that look the same in both.</summary>
    public static ThemedColor Fixed(Color color) => new(color, color);

    /// <summary><paramref name="light"/> and <paramref name="dark"/> as 0xRRGGBB, with optional alphas from 0 to 1.</summary>
    public static ThemedColor Hex(uint light, uint dark, double lightAlpha = 1, double darkAlpha = 1) =>
        new(Rgb(light, lightAlpha), Rgb(dark, darkAlpha));

    /// <summary>One fixed sRGB color (0xRRGGBB).</summary>
    public static ThemedColor Fixed(uint hex, double alpha = 1) => Fixed(Rgb(hex, alpha));

    /// <summary>
    /// The color of the brush <paramref name="key"/> in the app's <c>Light</c> and <c>Dark</c> theme dictionaries
    /// (<c>Default</c> counts as dark, as in WinUI's own), the last merged dictionary first, as <c>{ThemeResource}</c>
    /// finds it; a key outside any theme dictionary is fixed. Read once, from the UI thread after the App's constructor.
    /// </summary>
    public static ThemedColor Resource(string key) =>
        new(
            ResourceColor(key, "Light") ?? throw new KeyNotFoundException($"No light brush or color named {key}."),
            ResourceColor(key, "Dark") ?? ResourceColor(key, "Default") ?? throw new KeyNotFoundException($"No dark brush or color named {key}.")
        );

    private static Color? ResourceColor(string key, string theme)
    {
        return XamlResources.FindThemed(key, theme) is { } value ? ColorOf(value) : null;
    }

    private static Color? ColorOf(object value) =>
        XamlCast.As<SolidColorBrush>(value) is { } brush ? brush.Color
        : value is Color color ? color
        : null;

    private static Color Rgb(uint hex, double alpha) =>
        Color.FromArgb((byte)Math.Round(Math.Clamp(alpha, 0, 1) * 255), (byte)(hex >> 16), (byte)(hex >> 8), (byte)hex);
}

/// <summary>
/// The app's colors for the companion's shared controls (Swift's <c>CompanionPalette</c>): the character's paints, the
/// speech bubble, the status pill and the chrome. The UI roles follow the app's design (an accent, a secondary color,
/// paper and ink); <see cref="Paints"/> maps the paint roles its character art names to colors. The package's default,
/// <see cref="System"/>, is Windows' own colors, with every paint in the text color. Under a contrast theme the
/// controls take Windows' contrast colors instead, whatever the palette says (<see cref="Contrast"/>); the character keeps
/// its paints.
/// </summary>
public sealed record CompanionPalette(
    ThemedColor Paper,
    ThemedColor Paper2,
    ThemedColor Card,
    ThemedColor Ink,
    ThemedColor InkSoft,
    ThemedColor Line,
    ThemedColor Outline,
    ThemedColor Shade,
    ThemedColor Accent,
    ThemedColor AccentText,
    ThemedColor AccentSoft,
    ThemedColor Secondary,
    ThemedColor SecondaryText,
    ThemedColor SecondarySoft,
    ThemedColor OnInk,
    ThemedColor OnAccent,
    Func<PaintRole, ThemedColor> Paints
)
{
    /// <summary>Windows' colors (its fill and text tokens, the user's accent): the default until the app sets its own.</summary>
    public static CompanionPalette System { get; } = MakeSystem();

    /// <summary>
    /// The palette under a contrast theme: Windows' contrast colors for every UI role (the window, its text, the
    /// highlight), read when asked, and <paramref name="paints"/> for the character.
    /// </summary>
    public static CompanionPalette Contrast(Func<PaintRole, ThemedColor> paints)
    {
        var settings = new UISettings();
        ThemedColor Element(UIElementType type) => ThemedColor.Fixed(settings.UIElementColor(type));
        var window = Element(UIElementType.Window);
        var text = Element(UIElementType.WindowText);
        var highlight = Element(UIElementType.Highlight);
        var onHighlight = Element(UIElementType.HighlightText);
        var contrast = new CompanionPalette(
            Paper: window,
            Paper2: window,
            Card: window,
            Ink: text,
            InkSoft: text,
            Line: text,
            Outline: text,
            Shade: ThemedColor.Fixed(Color.FromArgb(0, 0, 0, 0)),
            Accent: highlight,
            AccentText: text,
            AccentSoft: window,
            Secondary: highlight,
            SecondaryText: text,
            SecondarySoft: window,
            OnInk: window,
            OnAccent: onHighlight,
            Paints: paints
        );
        return contrast with { IsContrast = true };
    }

    /// <summary>Whether this is the <see cref="Contrast"/> palette.</summary>
    public bool IsContrast { get; private init; }

    /// <summary>Whether Windows is in a contrast theme right now.</summary>
    public static bool ContrastThemeIsOn => new AccessibilitySettings().HighContrast;

    /// <summary>This palette, or <see cref="Contrast"/> with its paints while a contrast theme is on.</summary>
    public CompanionPalette ForCurrentContrast() => ContrastThemeIsOn && !IsContrast ? Contrast(Paints) : this;

    private static CompanionPalette MakeSystem()
    {
        var settings = new UISettings();
        var accent = settings.GetColorValue(UIColorType.Accent);
        var accentDark = settings.GetColorValue(UIColorType.AccentDark2);
        var accentLight = settings.GetColorValue(UIColorType.AccentLight2);
        var ink = ThemedColor.Hex(0x000000, 0xFFFFFF, lightAlpha: 0.894, darkAlpha: 1);
        return new(
            Paper: ThemedColor.Hex(0xF3F3F3, 0x202020),
            Paper2: ThemedColor.Hex(0xEEEEEE, 0x1C1C1C),
            Card: ThemedColor.Hex(0xFFFFFF, 0x2B2B2B),
            Ink: ink,
            InkSoft: ThemedColor.Hex(0x000000, 0xFFFFFF, lightAlpha: 0.62, darkAlpha: 0.786),
            Line: ThemedColor.Hex(0x000000, 0xFFFFFF, lightAlpha: 0.08, darkAlpha: 0.08),
            Outline: ThemedColor.Hex(0x000000, 0xFFFFFF, lightAlpha: 0.36, darkAlpha: 0.36),
            Shade: ThemedColor.Hex(0x000000, 0x000000, lightAlpha: 0.15, darkAlpha: 0.3),
            Accent: new(accentDark, accentLight),
            AccentText: new(accentDark, accentLight),
            AccentSoft: new(WithAlpha(accent, 0.2), WithAlpha(accent, 0.3)),
            Secondary: ThemedColor.Hex(0x0F7B0F, 0x6CCB5F),
            SecondaryText: ThemedColor.Hex(0x0F7B0F, 0x6CCB5F),
            SecondarySoft: ThemedColor.Hex(0x0F7B0F, 0x6CCB5F, lightAlpha: 0.2, darkAlpha: 0.3),
            OnInk: ThemedColor.Hex(0xFFFFFF, 0x000000),
            OnAccent: ThemedColor.Hex(0xFFFFFF, 0x000000),
            Paints: _ => ink
        );
    }

    private static Color WithAlpha(Color color, double alpha) => Color.FromArgb((byte)Math.Round(alpha * 255), color.R, color.G, color.B);
}
