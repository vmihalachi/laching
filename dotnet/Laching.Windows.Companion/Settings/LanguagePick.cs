using System.Globalization;
using System.Runtime.InteropServices;
using Laching.Windows.Shell.Localization;
using ApplicationLanguages = Microsoft.Windows.Globalization.ApplicationLanguages;
using NativeMethods = Laching.Windows.Companion.Shell.NativeMethods;

namespace Laching.Windows.Companion.Settings;

/// <summary>
/// <see cref="LanguageRow"/>'s state for the whole process: the language the process started in, the one picked since
/// (a Settings window opened again shows it), and where the pick is kept without a package. Kept by the row itself, never
/// read back from <c>ApplicationLanguages.PrimaryLanguageOverride</c> after a set (unpackaged, setting <c>""</c> throws
/// and leaves the old tag). Apart from the row, so <c>CompanionApp.Start</c> makes it before WinUI starts and tests reach
/// the logic.
/// </summary>
internal sealed class LanguagePick(string startedIn, LanguageSetting? setting)
{
    /// <summary>
    /// The process's: <c>CompanionApp.Start</c>'s when the app gave it a <see cref="LanguageSetting"/> and has no package,
    /// else the first row's (from the override then in effect, with no setting).
    /// </summary>
    public static LanguagePick? Process { get; set; }

    /// <summary>The language this process started in, as the override had it: <c>""</c> for Windows' language.</summary>
    public string StartedIn { get; } = startedIn;

    /// <summary>The tag picked since, <c>""</c> for Windows' language; <see cref="StartedIn"/> until something is picked.</summary>
    public string Picked { get; private set; } = startedIn;

    /// <summary>Where the pick is kept for the next start without a package; null when Windows keeps it (the override).</summary>
    public LanguageSetting? Setting { get; } = setting;

    /// <summary>Whether the next start runs in another language than this one, so the restart note shows.</summary>
    public bool ChangesAtRestart => !string.Equals(Picked, StartedIn, StringComparison.OrdinalIgnoreCase);

    /// <summary>The row's entry for <see cref="Picked"/> among <paramref name="tags"/>: 0 (Windows' language) when it isn't one.</summary>
    public int Entry(IReadOnlyList<string> tags) =>
        tags.ToList().FindIndex(tag => string.Equals(tag, Picked, StringComparison.OrdinalIgnoreCase)) + 1;

    /// <summary>Picks <paramref name="tag"/> (<c>""</c> for Windows' language) and keeps it in <see cref="Setting"/>, if any.</summary>
    public void Pick(string tag)
    {
        Picked = tag;
        Setting?.Store(tag);
    }

    /// <summary>
    /// The process's pick at its start, before WinUI starts: the tag stored in <paramref name="setting"/>, if the app
    /// still has it, becomes the override (so <c>Loc.Choose</c> picks it) and the process's preferred UI language (so
    /// WinUI's own words follow it: without a package they don't follow the override).
    /// </summary>
    public static LanguagePick Start(LanguageSetting setting) => Start(setting, CurrentOverride(), Apply);

    /// <summary>
    /// <see cref="Start(LanguageSetting)"/>'s rule, with the override read before any set
    /// (<paramref name="currentOverride"/>, <c>""</c> in a new process without a package) and the set itself
    /// (<paramref name="apply"/>, true when Windows took the tag) passed in, so tests reach it without WinUI: the stored
    /// tag becomes the language the process started in once applied, so the row shows it and no restart note; otherwise
    /// the process started in <paramref name="currentOverride"/>. Never applies a tag the app no longer has.
    /// </summary>
    internal static LanguagePick Start(LanguageSetting setting, string currentOverride, Func<string, bool> apply) =>
        new(setting.Stored is { } tag && apply(tag) ? tag : currentOverride, setting);

    /// <summary>The override in effect (<see cref="Loc.Override"/>), <c>""</c> for none or when it can't be read.</summary>
    public static string CurrentOverride()
    {
        try
        {
            return Loc.Override;
        }
        catch (COMException)
        {
            return "";
        }
    }

    /// <summary>
    /// The process's UI languages for <paramref name="tag"/>: the tag, then its specific culture when it is a neutral one
    /// ("de", "de-DE"), the form WinUI was seen to follow.
    /// </summary>
    public static IReadOnlyList<string> ProcessLanguages(string tag)
    {
        try
        {
            var specific = CultureInfo.CreateSpecificCulture(tag).Name;
            return specific.Length == 0 || string.Equals(specific, tag, StringComparison.OrdinalIgnoreCase) ? [tag] : [tag, specific];
        }
        catch (CultureNotFoundException)
        {
            return [tag];
        }
    }

    private static bool Apply(string tag)
    {
        try
        {
            ApplicationLanguages.PrimaryLanguageOverride = tag;
        }
        catch (COMException)
        {
            return false;
        }
        NativeMethods.SetProcessLanguages(ProcessLanguages(tag));
        return true;
    }
}
