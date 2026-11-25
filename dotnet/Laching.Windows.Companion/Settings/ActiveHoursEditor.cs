using System.Globalization;
using Laching.Companion.Settings;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Windows.Globalization.DateTimeFormatting;

namespace Laching.Windows.Companion.Settings;

/// <summary>
/// The active hours' from and to time pickers and the weekday chips, in the order the user's culture starts its week
/// (Swift's <c>ActiveHoursEditor</c>), for Settings and onboarding. Every word is the app's (<see cref="Copy"/>); the chips
/// take its <see cref="ChipStyle"/>. The pickers stack under the label, each with its own ("From", "To"): two WinUI time
/// pickers don't fit side by side in a companion's window (their minutes get cut off), where the Mac puts them in one
/// row. Narrator reads the editor as a group named by <see cref="ActiveHoursCopy.ActiveHours"/>, the pickers by their
/// labels and the weekday chips, with their full day names, as a group named by <see cref="ActiveHoursCopy.Days"/>; the
/// labels on screen are left out, so nothing is read twice. At exactly 0:00 WinUI leaves the time out of a picker's
/// name ("From  time picker"), so the editor puts it back in WinUI's words.
/// </summary>
public sealed partial class ActiveHoursEditor : UserControl
{
    private readonly TextBlock label = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly TimePicker from = new() { MinuteIncrement = 5, MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly TimePicker to = new() { MinuteIncrement = 5, MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly TextBlock fromLabel = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock toLabel = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock daysLabel = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly (int Weekday, ToggleButton Chip)[] chips;
    private readonly GroupGrid days;
    private readonly SettingsWatch watch;
    private CompanionSettings? settings;
    private bool refreshing;

    public ActiveHoursEditor()
    {
        IsTabStop = false;
        from.SelectedTimeChanged += (_, e) => SetMinute(e.NewTime, start: true);
        to.SelectedTimeChanged += (_, e) => SetMinute(e.NewTime, start: false);
        KeepMidnightNamed(from);
        KeepMidnightNamed(to);

        var pickers = new Grid { ColumnSpacing = Space.Item, RowSpacing = Space.Snug };
        pickers.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        pickers.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        pickers.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        pickers.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        pickers.Children.Add(fromLabel);
        Grid.SetColumn(from, 1);
        pickers.Children.Add(from);
        Grid.SetRow(toLabel, 1);
        pickers.Children.Add(toLabel);
        Grid.SetRow(to, 1);
        Grid.SetColumn(to, 1);
        pickers.Children.Add(to);

        // Seven chips in the culture's week order, made once: remaking them on a click would drop the keyboard focus.
        var format = CultureInfo.CurrentCulture.DateTimeFormat;
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = Space.Snug, HorizontalAlignment = HorizontalAlignment.Right };
        chips =
        [
            .. OrderedWeekdays((int)format.FirstDayOfWeek + 1)
                .Select(weekday =>
                {
                    var day = (DayOfWeek)(weekday - 1);
                    var chip = HighlightFill.Plain(new ToggleButton { Content = format.GetShortestDayName(day) });
                    AutomationProperties.SetName(chip, format.GetDayName(day));
                    chip.Click += (_, _) => SetWeekday(weekday, chip.IsChecked == true);
                    row.Children.Add(chip);
                    return (weekday, chip);
                }),
        ];
        var days = new GroupGrid { ColumnSpacing = Space.Item };
        days.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        days.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        days.Children.Add(daysLabel);
        Grid.SetColumn(row, 1);
        days.Children.Add(row);

        // The group, the pickers and the days carry these words for Narrator.
        foreach (var shown in new[] { label, fromLabel, toLabel, daysLabel })
            AutomationProperties.SetAccessibilityView(shown, AccessibilityView.Raw);
        this.days = days;
        var stack = new StackPanel { Spacing = Space.Item };
        stack.Children.Add(label);
        stack.Children.Add(pickers);
        stack.Children.Add(days);
        Content = stack;
        watch = new SettingsWatch(
            this,
            () => settings,
            Refresh,
            nameof(CompanionSettings.ActiveStartMinute),
            nameof(CompanionSettings.ActiveEndMinute),
            nameof(CompanionSettings.ActiveWeekdays)
        );
    }

    public CompanionSettings? Settings
    {
        get => settings;
        set
        {
            settings = value;
            watch.Reset();
        }
    }

    /// <summary>The editor's words, shown on screen and read by Narrator as the names of the editor, its pickers and its days.</summary>
    public ActiveHoursCopy? Copy
    {
        get;
        set
        {
            field = value;
            label.Text = value?.ActiveHours ?? "";
            fromLabel.Text = value?.From ?? "";
            toLabel.Text = value?.To ?? "";
            daysLabel.Text = value?.Days ?? "";
            AutomationProperties.SetName(this, value?.ActiveHours ?? "");
            AutomationProperties.SetName(from, value?.From ?? "");
            AutomationProperties.SetName(to, value?.To ?? "");
            AutomationProperties.SetName(days, value?.Days ?? "");
        }
    }

    /// <summary>The weekday chips' style (a <c>ToggleButton</c> style, such as the app's chip); WinUI's toggle button when null.</summary>
    public Style? ChipStyle
    {
        get;
        set
        {
            field = value;
            foreach (var (_, chip) in chips)
                chip.Style = value;
        }
    }

    /// <summary>The weekdays (1 = Sunday … 7 = Saturday, as the settings store them) from <paramref name="firstWeekday"/> on.</summary>
    public static IReadOnlyList<int> OrderedWeekdays(int firstWeekday) => Enumerable.Range(0, 7).Select(n => (firstWeekday - 1 + n) % 7 + 1).ToArray();

    /// <summary>A minute of the day as a time picker's time, and back.</summary>
    public static TimeSpan Time(int minute) => TimeSpan.FromMinutes(Math.Clamp(minute, 0, 24 * 60 - 1));

    public static int Minute(TimeSpan time) => (int)Math.Round(time.TotalMinutes) % (24 * 60);

    /// <summary>
    /// A time picker's button name as WinUI writes it at 0:00, where its time is left out, with <paramref name="time"/>
    /// back where the time goes: in the gap after the picker's name (<paramref name="picker"/>), which is followed by two
    /// spaces ("From  time picker" becomes "From 00:00 time picker") or ends the name (Italian's "Selezione ora From "
    /// becomes "Selezione ora From 00:00"); at the end when there's no such gap.
    /// </summary>
    internal static string WithTime(string name, string picker, string time)
    {
        var at = name.IndexOf(picker + "  ", StringComparison.Ordinal);
        if (at < 0 && name.EndsWith(picker + " ", StringComparison.Ordinal))
            at = name.Length - picker.Length - 1;
        if (at < 0)
            return $"{name.TrimEnd()} {time}";
        var cut = at + picker.Length + 1;
        return (name[..cut] + time + name[cut..]).Trim();
    }

    protected override AutomationPeer OnCreateAutomationPeer() => new GroupPeer(this, nameof(ActiveHoursEditor));

    private void SetMinute(TimeSpan? time, bool start)
    {
        if (refreshing || settings is null || time is not { } t)
            return;
        if (start)
            settings.ActiveStartMinute = Minute(t);
        else
            settings.ActiveEndMinute = Minute(t);
    }

    // WinUI names a time picker's button "<picker's name> <time> time picker" (in Windows' language), but formats the time
    // only when it isn't zero, so at 0:00 Narrator would read "From  time picker". WinUI renames the button when the
    // template is applied and after raising SelectedTimeChanged, so this names it again once loaded and, posted, after
    // each change.
    private static void KeepMidnightNamed(TimePicker picker)
    {
        void Later() => picker.DispatcherQueue.TryEnqueue(() => NameMidnight(picker));
        picker.Loaded += (_, _) => Later();
        picker.SelectedTimeChanged += (_, _) => Later();
    }

    private static void NameMidnight(TimePicker picker)
    {
        if (picker.SelectedTime != TimeSpan.Zero || Named(picker, "FlyoutButton") is not { } button)
            return;
        var name = AutomationProperties.GetName(button);
        var time = ShortTime(picker.ClockIdentifier);
        if (name.Length > 0 && !name.Contains(time, StringComparison.Ordinal))
            AutomationProperties.SetName(button, WithTime(name, AutomationProperties.GetName(picker), time));
    }

    // The template part named so, searched depth first.
    private static FrameworkElement? Named(DependencyObject node, string name)
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
        {
            var child = VisualTreeHelper.GetChild(node, i);
            if (XamlCast.As<FrameworkElement>(child) is { } element && element.Name == name)
                return element;
            if (Named(child, name) is { } found)
                return found;
        }
        return null;
    }

    // 0:00 as WinUI writes a picker's time: the system's short time with the picker's clock, on WinUI's sentinel day.
    private static string ShortTime(string clock)
    {
        var system = new DateTimeFormatter("shorttime");
        return new DateTimeFormatter("shorttime", system.Languages, system.GeographicRegion, system.Calendar, clock).Format(
            new DateTimeOffset(new DateTime(2011, 7, 21, 0, 0, 0, DateTimeKind.Local))
        );
    }

    private void SetWeekday(int weekday, bool on)
    {
        if (settings is null)
            return;
        var days = new HashSet<int>(settings.ActiveWeekdays);
        if (on ? days.Add(weekday) : days.Remove(weekday))
            settings.ActiveWeekdays = days;
    }

    private void Refresh()
    {
        if (settings is not { } s)
            return;
        refreshing = true;
        from.SelectedTime = Time(s.ActiveStartMinute);
        // The end of the day (1440) shows as the last minute.
        to.SelectedTime = Time(s.ActiveEndMinute);
        refreshing = false;
        foreach (var (weekday, chip) in chips)
            chip.IsChecked = s.ActiveWeekdays.Contains(weekday);
    }
}
