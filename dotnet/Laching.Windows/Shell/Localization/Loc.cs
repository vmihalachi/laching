using System.Diagnostics;
using System.Globalization;
using Laching.Core.Localization;
using Microsoft.Windows.ApplicationModel.Resources;

namespace Laching.Windows.Shell.Localization;

/// <summary>
/// Looks up user-facing text by its catalog key, like <c>String(localized:)</c> on the Mac, in the app's <c>.resw</c>
/// resources (MRT Core). Names are <see cref="ReswName"/>-escaped keys, placeholders are <c>{0}</c>, and plural
/// variants are <c>name__one</c>, <c>name__other</c> and so on, picked with <see cref="PluralRules"/>. The package
/// ships no strings of its own: a missing string falls back to the English key, so nothing ever shows up blank.
/// </summary>
public static class Loc
{
    private static readonly ResourceManager Manager = new();
    private static readonly MrtTable Table = new();
    private static ResourceMap? strings;

    /// <summary>The language lookups resolve in, once <see cref="UseLanguage"/> chose one; else MRT's default.</summary>
    private static ResourceContext? context;

    /// <summary>The language the strings resolve to, for plural rules and formatting.</summary>
    public static string Language { get; private set; } = "en";

    public static CultureInfo Culture => CultureInfo.CurrentCulture;

    /// <summary>
    /// Which resource subtree holds the strings: the <c>.resw</c> file's name, "Resources" for the usual
    /// <c>Strings/en/Resources.resw</c>. Call once at launch, before the first lookup (which otherwise assumes "Resources").
    /// </summary>
    public static void Configure(string resourceSubtree)
    {
        strings = Manager.MainResourceMap.GetSubtree(resourceSubtree);
    }

    /// <summary>
    /// Sets the language once at launch: the strings, the plural rules and the .NET culture (dates, numbers) all
    /// follow it, so <see cref="Language"/> never disagrees with the words shown.
    /// </summary>
    public static void UseLanguage(string language)
    {
        Language = language;
        var culture = CultureInfo.GetCultureInfo(language);
        CultureInfo.DefaultThreadCurrentCulture = culture;
        CultureInfo.DefaultThreadCurrentUICulture = culture;
        CultureInfo.CurrentCulture = culture;
        CultureInfo.CurrentUICulture = culture;
        context = Manager.CreateResourceContext();
        context.QualifierValues["Language"] = language;
    }

    /// <summary>
    /// The language to run in, out of the app's <paramref name="languages"/> (its <c>Strings</c> folders, the default
    /// first): the one picked in the app (<c>ApplicationLanguages.PrimaryLanguageOverride</c>, which <c>LanguageRow</c>
    /// sets with a package and <c>CompanionApp.Start</c> sets from the app's <see cref="LanguageSetting"/> without one),
    /// else the first of Windows' preferred languages the app has, else the default. Pass the result to
    /// <see cref="UseLanguage"/>.
    /// </summary>
    public static string Choose(IReadOnlyList<string> languages) =>
        LanguageMatch.Choose(languages, Override, global::Windows.System.UserProfile.GlobalizationPreferences.Languages);

    /// <summary>
    /// The language picked in the app, <c>""</c> for none (Windows' languages). With package identity it is Windows'
    /// <c>ApplicationLanguages.PrimaryLanguageOverride</c>, kept for the package, read and set there: the Windows App SDK's
    /// getter only returns what this process set, so a pick made before a restart read as none, and its setter refuses
    /// <c>""</c>. Without one it is the Windows App SDK's, which lasts for the process and refuses <c>""</c> (an
    /// <see cref="ArgumentException"/>). The set applies at the next start: this run keeps the language
    /// <see cref="UseLanguage"/> gave it.
    /// </summary>
    public static string Override
    {
        get =>
            (
                PackageIdentity.IsPackaged
                    ? global::Windows.Globalization.ApplicationLanguages.PrimaryLanguageOverride
                    : Microsoft.Windows.Globalization.ApplicationLanguages.PrimaryLanguageOverride
            ) ?? "";
        set
        {
            if (PackageIdentity.IsPackaged)
                global::Windows.Globalization.ApplicationLanguages.PrimaryLanguageOverride = value;
            else
                Microsoft.Windows.Globalization.ApplicationLanguages.PrimaryLanguageOverride = value;
        }
    }

    public static string Get(string key) => Table.Get(key);

    /// <summary>
    /// A phrase's text: its translation under <paramref name="key"/> (<c>phrase.&lt;id&gt;</c>), else
    /// <paramref name="source"/>, the line as the bank has it. Unlike <see cref="Get(string)"/> it never shows the key.
    /// </summary>
    public static string Localize(string key, string source) => Table.Localize(key, source);

    public static string Format(string key, params object[] args) => Table.Format(key, args);

    /// <summary>A plural string. <paramref name="count"/> picks the variant; pass it in <paramref name="args"/> too.</summary>
    public static string Plural(string key, int count, params object[] args) => Table.Plural(key, count, args);

    /// <summary>
    /// Text for a key pure code returned. <see cref="LocalizedKey.ArgumentKind.Int"/> arguments are numbers (the first
    /// one also picks the plural variant when the string has variants);
    /// <see cref="LocalizedKey.ArgumentKind.DurationMinutes"/> arguments go through <see cref="Formats.DurationMinutes"/>.
    /// </summary>
    public static string Get(LocalizedKey key)
    {
        if (key.Args.Count == 0)
            return Get(key.Key);
        var args = key.Args.Select(FormatArg).ToArray();
        var count = key.Args.FirstOrDefault(a => a.Type == LocalizedKey.ArgumentKind.Int)?.Value;
        return count is { } n && IsPlural(key.Key) ? Plural(key.Key, n, args) : Format(key.Key, args);
    }

    /// <summary>Upper case in the current language, for labels and headings.</summary>
    public static string Upper(string text) => text.ToUpper(Culture);

    private static object FormatArg(LocalizedKey.Argument arg) =>
        arg.Type switch
        {
            LocalizedKey.ArgumentKind.Int => arg.Value,
            LocalizedKey.ArgumentKind.DurationMinutes => Formats.DurationMinutes(arg.Value),
            _ => throw new ArgumentOutOfRangeException(nameof(arg), arg.Type, "Unknown argument kind"),
        };

    private static bool IsPlural(string key) => Lookup($"{ReswName.For(key)}__other") is not null;

    private static string? Lookup(string name)
    {
        strings ??= Manager.MainResourceMap.GetSubtree("Resources");
        return (context is null ? strings.TryGetValue(name) : strings.TryGetValue(name, context))?.ValueAsString;
    }

    /// <summary>The shared lookup, plural and fallback logic over MRT Core.</summary>
    private sealed class MrtTable : StringTable
    {
        public override string Language => Loc.Language;

        public override CultureInfo Culture => Loc.Culture;

        protected override string? Lookup(string name) => Loc.Lookup(name);

        protected override void OnMissing(string key) => Debug.WriteLine($"Missing string: {key}");
    }
}
