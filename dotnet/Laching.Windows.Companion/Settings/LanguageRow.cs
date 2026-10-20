using System.Globalization;
using System.Runtime.InteropServices;
using Laching.Windows.Companion.Shell;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell.Localization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using AppInstance = Microsoft.Windows.AppLifecycle.AppInstance;

namespace Laching.Windows.Companion.Settings;

/// <summary>
/// The language the app speaks (Swift's <c>LanguageRow</c>). The Mac sends the user to System Settings' per-app list;
/// Windows has no such page for every app, so this is a list in the app: Windows' language first, then each of the
/// app's in its own words ("Deutsch", "Português (Brasil)"). A new language applies at the next start, so a note and a
/// restart button show while the pick differs from the language this run started in (Narrator hears the note as it
/// appears, and again as the button's help text), and a Settings window opened again shows the pick. With package
/// identity the pick is Windows' <c>ApplicationLanguages.PrimaryLanguageOverride</c> (<see cref="Loc.Override"/>), which Windows keeps.
/// Without one that override lasts only for the process, so the pick goes into the app's settings instead (the
/// <see cref="LanguageSetting"/> the app passed to <see cref="CompanionApp.Start"/>, which sets it again at the next
/// start); an app that passes none comes back in Windows' language after a restart. The words are the app's; the button
/// takes its <see cref="ButtonStyle"/>.
/// </summary>
public sealed partial class LanguageRow : UserControl
{
    private readonly TextBlock label = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly ComboBox choices = new() { MinWidth = 160, HorizontalAlignment = HorizontalAlignment.Right };
    private readonly TextBlock note = new() { FontSize = 12, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
    private readonly Button restart = new();
    private readonly Grid restartRow = new() { ColumnSpacing = Space.Item, Visibility = Visibility.Collapsed };
    private readonly TextBlock refused = new() { FontSize = 12, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
    private IReadOnlyList<string> tags = [];
    private bool updating;

    public LanguageRow()
    {
        IsTabStop = false;
        var row = new Grid { ColumnSpacing = Space.Item };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(label);
        // The control carries the label for Narrator, which would otherwise read it twice.
        AutomationProperties.SetAccessibilityView(label, Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
        Grid.SetColumn(choices, 1);
        row.Children.Add(choices);
        restartRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        restartRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        restartRow.Children.Add(note);
        Grid.SetColumn(restart, 1);
        restartRow.Children.Add(restart);
        var stack = new StackPanel { Spacing = Space.Snug };
        stack.Children.Add(row);
        stack.Children.Add(restartRow);
        stack.Children.Add(refused);
        Content = stack;
        choices.SelectionChanged += (_, _) => Choose();
        restart.Click += (_, _) => (OnRestart ?? DefaultRestart)();
        LookBinding.Keep(
            this,
            () =>
            {
                note.Foreground = LookBinding.Brush(this, p => p.InkSoft);
                refused.Foreground = LookBinding.Brush(this, p => p.AccentText);
            }
        );
    }

    /// <summary>The languages the app has, as BCP-47 tags ("en", "de", "pt-BR"); the list follows this order after Windows' own.</summary>
    public IReadOnlyList<string> Languages
    {
        get => tags;
        set
        {
            tags = value;
            Rebuild();
        }
    }

    public LanguageCopy? Copy
    {
        get;
        set
        {
            field = value;
            label.Text = value?.Label ?? "";
            AutomationProperties.SetName(choices, value?.Label ?? "");
            note.Text = value?.RestartNote ?? "";
            restart.Content = value?.Restart;
            // The button says why it's there: the note beside it.
            AutomationProperties.SetHelpText(restart, value?.RestartNote ?? "");
            refused.Text = value?.RestartRefused ?? "";
            Rebuild();
        }
    }

    public Style? ButtonStyle
    {
        get => restart.Style;
        set => restart.Style = value;
    }

    /// <summary>
    /// Restarts the app in its new language. Unset, the Windows App SDK's <c>AppInstance.Restart</c>; when Windows refuses
    /// (the app isn't in front, a restart is already pending), <see cref="LanguageCopy.RestartRefused"/> shows under it.
    /// </summary>
    public Action? OnRestart { get; set; }

    /// <summary>A language's name in its own words, capitalized as a list item ("Deutsch", "Español"); the tag itself when unknown.</summary>
    public static string NativeName(string tag)
    {
        try
        {
            // Predefined only: otherwise any tag makes up a culture named after itself.
            var culture = CultureInfo.GetCultureInfo(tag, predefinedOnly: true);
            var name = culture.NativeName;
            return name.Length == 0 ? tag : culture.TextInfo.ToUpper(name[0]) + name[1..];
        }
        catch (CultureNotFoundException)
        {
            return tag;
        }
    }

    /// <summary>The process's pick: <see cref="CompanionApp.Start"/>'s, else made at the first row from the override in effect.</summary>
    private static LanguagePick Pick => LanguagePick.Process ??= new LanguagePick(LanguagePick.CurrentOverride(), null);

    private void Rebuild()
    {
        updating = true;
        choices.Items.Clear();
        choices.Items.Add(Copy?.SystemLanguage ?? "");
        foreach (var tag in tags)
            choices.Items.Add(NativeName(tag));
        choices.SelectedIndex = Pick.Entry(tags);
        updating = false;
        UpdateRestart();
    }

    private void Choose()
    {
        if (updating || choices.SelectedIndex < 0)
            return;
        var tag = choices.SelectedIndex == 0 ? "" : tags[choices.SelectedIndex - 1];
        var pick = Pick;
        pick.Pick(tag);
        if (pick.Setting is null)
        {
            try
            {
                Loc.Override = tag;
            }
            // Without a package "" (Windows' language) is refused; the row shows the pick either way.
            catch (Exception e) when (e is ArgumentException or COMException) { }
        }
        UpdateRestart();
    }

    private void UpdateRestart()
    {
        var before = restartRow.Visibility;
        restartRow.Visibility = Pick.ChangesAtRestart ? Visibility.Visible : Visibility.Collapsed;
        refused.Visibility = Visibility.Collapsed;
        if (Announcer.Appears(before, restartRow.Visibility))
            Announcer.Say(note, Copy?.RestartNote, Announcer.Note);
    }

    private void DefaultRestart()
    {
        // Returns only when Windows refused: a restart ends this process.
        _ = AppInstance.Restart("");
        refused.Visibility = string.IsNullOrEmpty(Copy?.RestartRefused) ? Visibility.Collapsed : Visibility.Visible;
    }
}
