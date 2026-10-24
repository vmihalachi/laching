using Laching.Windows.Shell;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI.Text;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// The app's look for the package's controls (on the Mac, <c>companionWindowRoot</c>'s palette and display face): its
/// <see cref="CompanionPalette"/>, and the display face the speech bubble speaks in. The app sets <see cref="Current"/>
/// once, in <c>MakeCompanion</c> before any window opens; the controls read it, and repaint when it changes, when their
/// theme changes and when Windows' contrast theme turns on or off. The windows' background is its paper.
/// </summary>
public sealed record CompanionLook(CompanionPalette Palette)
{
    private static CompanionLook? current;

    /// <summary>The display face, such as the app's own font from its package; null keeps the control's font.</summary>
    public FontFamily? DisplayFont { get; init; }

    /// <summary>The display face's weight: medium by default, as the Mac's rounded system face.</summary>
    public FontWeight DisplayWeight { get; init; } = new() { Weight = 500 };

    /// <summary>The app's look; Windows' colors and font until the app sets its own.</summary>
    public static CompanionLook Current
    {
        get => current ??= new CompanionLook(CompanionPalette.System);
        set
        {
            current = value;
            ThemeBinding.Refresh();
        }
    }

    /// <summary>The palette to paint with right now: <see cref="Palette"/>, or Windows' contrast colors under a contrast theme.</summary>
    public CompanionPalette PaletteNow => Palette.ForCurrentContrast();
}

/// <summary>
/// Keeps a code-built element in the look's colors (what <c>{ThemeResource}</c> does in XAML): <c>apply</c> runs now,
/// when the element's theme changes and, while it is loaded, when the look or Windows' colors change.
/// </summary>
public static class LookBinding
{
    /// <summary>Applies now and on element, app-look and Windows color changes; listeners follow the element's lifetime.</summary>
    public static void Keep(FrameworkElement element, Action apply) => ThemeBinding.Keep(element, apply);

    /// <summary>A brush of <paramref name="color"/> picked for <paramref name="element"/>'s theme from the look's palette now.</summary>
    public static SolidColorBrush Brush(FrameworkElement element, Func<CompanionPalette, ThemedColor> color) =>
        new(color(CompanionLook.Current.PaletteNow).For(element.ActualTheme));
}

/// <summary>
/// A control that WinUI fills with Windows' highlight color in a contrast theme (an accent button, a checked chip) keeps
/// its style's own colors for its words. Under the app's default <c>HighContrastAdjustment</c> (<c>Auto</c>) WinUI draws
/// every word in the window's text color on a box of the window's color, the backplate: unseen on the window's own
/// background, a box behind each word on the highlight. WinUI's accent button and toggle button already pair each fill
/// with its text in their <c>HighContrast</c> resources (the highlight with the highlight text, and their own pairs when
/// pointed at or pressed), so these controls turn the backplate off. Nothing changes outside a contrast theme.
/// </summary>
internal static class HighlightFill
{
    public static T Plain<T>(T control)
        where T : UIElement
    {
        control.HighContrastAdjustment = ElementHighContrastAdjustment.None;
        return control;
    }
}
