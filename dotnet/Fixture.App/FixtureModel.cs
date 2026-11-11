using Laching.Companion.Cadence;
using Laching.Companion.Desktop;
using Laching.Companion.Figures;
using Laching.Companion.Phrases;
using Laching.Companion.Settings;
using Laching.Companion.Shell.App;
using Laching.Companion.Shell.Desktop;
using Laching.Companion.Shell.Performance;
using Laching.Companion.Shell.Signals;
using Laching.Companion.Shell.Speech;
using Laching.Core.Scheduling;
using Laching.Core.Store;
using Laching.Testing.Fixtures;
using Laching.Windows.Companion.Shell;
using Laching.Windows.Companion.Views;
using Size = Windows.Foundation.Size;

namespace FixtureApp;

/// <summary>
/// The fixture app's model: Laching's fixture bank and its vector character in the tray, placeholder words, and what the
/// hand checks need besides (a line long enough to start a call during, a clock that skips to the next line).
/// </summary>
public sealed class FixtureModel : CompanionAppModel<FixtureWindow, CompanionSettings>
{
    public const string Supporter = "fixture.supporter";

    public const string SayMore = "SAY_MORE";

    public const string ShhHour = "SHH_HOUR";

    /// <summary>
    /// The fixture's defaults, made for checking by hand: every hour of every day is active.
    /// </summary>
    public static readonly CompanionDefaults Defaults = FixtureDefaults.Value with
    {
        ActiveStartMinute = 0,
        ActiveEndMinute = 24 * 60,
        ActiveWeekdays = new HashSet<int> { 1, 2, 3, 4, 5, 6, 7 },
    };

    public static new readonly CompanionAppConfiguration Configuration = new(
        StatusItemTooltip: "Laching fixture",
        AccessibilityLabel: "Fixture",
        Sounds: new CompanionSounds(Blip: FixtureSounds.Blip, Shh: FixtureSounds.Shh, Hello: FixtureSounds.Hello),
        Status: FixtureCopy.Status("t"),
        Desktop: FixtureCopy.Desktop("t"),
        TuneBubbleText: () => "♪",
        Localize: (_, source) => source,
        SupporterProduct: Supporter);

    /// <summary>Long enough to start a call while it plays.</summary>
    private static readonly Phrase LongLine = new(
        "fixture-long",
        new PhraseKind.Line(
            "This is the fixture's long line, so there is time to start a call while it plays. Open the Camera app or "
                + "join a call now: the line should stop within a second, and the status should say you're on a call. If "
                + "you can still hear this sentence, the call wasn't noticed."),
        new[] { Tag.General });

    private readonly IClock clock;
    private readonly ITimers timers;
    private string clockLine = "";
    private IReadOnlyList<string> sayingParts = [];
    private string bubbleHeader = "";
    private string statusKind = "";

    public FixtureModel(
        CompanionSettings settings,
        PhraseBank bank,
        CharacterArt art,
        CharacterArt rasterArt,
        WindowsCompanionHost<FixtureWindow> host,
        IClock clock,
        IIdleTimeProvider idle,
        IBusyStateProvider busy,
        ISpeaking speaker,
        IChimePlaying chime,
        ICompanionNotifying notifier,
        SignalHub signals,
        PurchaseStore purchases,
        ulong? seed = null)
        : base(
            settings,
            bank,
            FixtureCopy.Roles,
            Configuration,
            host,
            clock,
            idle,
            busy,
            TimeZoneInfo.Local,
            speaker,
            chime,
            notifier,
            signals,
            purchases,
            new VoiceLibrary(null, "en", allowTesting: false),
            seed)
    {
        this.clock = clock;
        timers = host.Timers;
        DesktopLayout = new DesktopLayout(art);
        GlyphCharacter = new RiggedCharacter(art);
        RasterCharacter = new RiggedCharacter(rasterArt);
        StatusKind = KindOfStatus();
        PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(StatusLine))
                StatusKind = KindOfStatus();
            else if (e.PropertyName == nameof(JustMuted))
                OnPropertyChanged(nameof(HasJustMuted));
            else if (e.PropertyName == nameof(BubblePhraseId))
                OnPropertyChanged(nameof(HasBubblePhrase));
        };
    }

    /// <summary>The fixture's paints (<c>Tests/Fixtures/characters/palette.json</c>) over Windows' colors.</summary>
    public static CompanionPalette Palette { get; } =
        CompanionPalette.System with
        {
            Paints = role =>
                role.RawValue switch
                {
                    "body" => ThemedColor.Fixed(0xFFD25A),
                    "edge" => ThemedColor.Fixed(0xEBAA2E),
                    "eye" => ThemedColor.Fixed(0x2B1A0E),
                    "blush" => ThemedColor.Fixed(0xF4A08C),
                    "stem" => ThemedColor.Fixed(0x6E9E45),
                    "inkSoft" => ThemedColor.Hex(0x7A6A55, 0xB8A890),
                    _ => ThemedColor.Hex(0x000000, 0xFFFFFF),
                },
        };

    /// <summary>The menus' words, with their access keys marked (the host's <c>MenuAccessKeys</c> is on).</summary>
    public override MenuCopy MenuCopy { get; } = FixtureCopy.Menu(accessKeys: true);

    public override DesktopLayout DesktopLayout { get; }

    public override RiggedCharacter GlyphCharacter { get; }

    /// <summary>The raster fixture, drawn on Home from its PNGs.</summary>
    public RiggedCharacter RasterCharacter { get; }

    /// <summary>How far the fixture's clock runs ahead of the wall clock, once skipped.</summary>
    public string ClockLine { get => clockLine; private set => Set(ref clockLine, value); }

    /// <summary>
    /// A joke's setup while its punchline is in the bubble (the bubbles' header); empty otherwise. Never null: x:Bind
    /// skips a function binding whose argument turns null, so a null would leave the last setup showing.
    /// </summary>
    public string BubbleHeader { get => bubbleHeader; private set => Set(ref bubbleHeader, value); }

    /// <summary>A line was just muted, so Home offers Undo (x:Bind can't follow <see cref="CompanionAppModel.JustMuted"/> back to null).</summary>
    public bool HasJustMuted => JustMuted is not null;

    /// <summary>The bubble shows a phrase that can be muted (x:Bind can't follow <see cref="CompanionAppModel.BubblePhraseId"/> back to null).</summary>
    public bool HasBubblePhrase => !string.IsNullOrEmpty(BubblePhraseId);

    /// <summary>
    /// The status pill's kind: the status line as it reads with the countdown at its last minute, so it changes with the
    /// status (Shh, a call, off hours, back to the countdown) and not as the minutes count down.
    /// </summary>
    public string StatusKind { get => statusKind; private set => Set(ref statusKind, value); }

    /// <summary>Keeps the phrase's parts, so the bubble can show a joke's setup over its punchline.</summary>
    public override void WillSay(Phrase phrase) => sayingParts = phrase.LocalizedParts(base.Configuration.Localize);

    /// <summary>The setup goes to the header while a later part shows; a line or a tune has none.</summary>
    public override void ShowBubble(string text)
    {
        var index = sayingParts.ToList().IndexOf(text);
        BubbleHeader = index > 0 ? sayingParts[0] : "";
        base.ShowBubble(text);
    }

    /// <summary>Each window's title and size.</summary>
    public static WindowSpec Spec(FixtureWindow id) =>
        id switch
        {
            FixtureWindow.Home => new WindowSpec("Laching fixture", new Size(440, 620)),
            FixtureWindow.Onboarding => new WindowSpec("Welcome", new Size(420, 320), IsExclusive: true),
            FixtureWindow.Settings => new WindowSpec("Settings", new Size(420, 600)),
            FixtureWindow.Support => new WindowSpec("Support", new Size(460, 580)),
            _ => throw new ArgumentOutOfRangeException(nameof(id), id, null),
        };

    public override void HandleNotificationAction(string id)
    {
        switch (id)
        {
            case SayMore:
                SayNow();
                break;
            case ShhHour:
                Shush(ChatterCadence.Shush.Hour);
                break;
        }
    }

    public void ShushForAnHour() => Shush(ChatterCadence.Shush.Hour);

    public void SayLongLine() => Say(LongLine);

    /// <summary>Skips the clock to the next line's time, then ticks: the line comes now, unless something holds it.</summary>
    public void MakeLineDue()
    {
        if (clock is SkippingClock skipping && Coordinator.Scheduler.NextFireDate is { } next && next > skipping.Now)
        {
            skipping.Skip(next - skipping.Now + TimeSpan.FromSeconds(1));
            ClockLine = $"The clock runs {skipping.Skipped:h\\:mm\\:ss} ahead.";
        }
        Tick();
    }

    private string KindOfStatus()
    {
        var status = Coordinator.Scheduler.Status;
        var at = status is SchedulerStatus.Scheduled scheduled ? scheduled.Date : clock.Now;
        return StatusLineFor(status, Signals.Snapshot(), at, TimeZone, Coordinator.Scheduler.EndOfToday, base.Configuration.Status);
    }

    /// <summary><see cref="MakeLineDue"/> in 10 s: time to go full screen.</summary>
    public void MakeLineDueSoon() => timers.After(10, MakeLineDue);

    /// <summary>The character on the desktop, on or off (the tray menu's "On the desktop" has the rest).</summary>
    public void ToggleDesktop() => SetShowOnDesktop(!Settings.ShowOnDesktop);

    /// <summary>The tray panel, as a click on the icon opens it (for when the icon sits in the overflow).</summary>
    public void OpenPanel() => StatusItem?.OpenPanel();
}

/// <summary>The wall clock, skipped ahead by hand (the scheduler knows the time only through it).</summary>
public sealed class SkippingClock : IClock
{
    public TimeSpan Skipped { get; private set; }

    public DateTimeOffset Now => DateTimeOffset.Now + Skipped;

    public void Skip(TimeSpan span) => Skipped += span;
}
