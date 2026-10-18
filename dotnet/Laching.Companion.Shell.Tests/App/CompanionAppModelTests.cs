using System.ComponentModel;
using Laching.Companion.Cadence;
using Laching.Companion.Desktop;
using Laching.Companion.Figures;
using Laching.Companion.Phrases;
using Laching.Companion.Rules;
using Laching.Companion.Settings;
using Laching.Companion.Shell.App;
using Laching.Companion.Shell.Desktop;
using Laching.Companion.Shell.Performance;
using Laching.Companion.Shell.Signals;
using Laching.Companion.Shell.Speech;
using Laching.Core.Scheduling;
using Laching.Core.Settings;
using Laching.Core.Store;

namespace Laching.Companion.Shell.Tests.App;

/// <summary>An app's model in another assembly: a subclass with state of its own and the hooks an app overrides.</summary>
internal sealed class FixtureModel(
    CompanionSettings settings,
    PhraseBank bank,
    ICompanionHost<FixtureWindow> host,
    IClock clock,
    IIdleTimeProvider idle,
    IBusyStateProvider busy,
    ISpeaking speaker,
    IChimePlaying chime,
    ICompanionNotifying notifier,
    SignalHub signals,
    PurchaseStore purchases)
    : CompanionAppModel<FixtureWindow, CompanionSettings>(
        settings,
        bank,
        FixtureCopy.Roles,
        Configuration,
        host,
        clock,
        idle,
        busy,
        TimeZoneInfo.Utc,
        speaker,
        chime,
        notifier,
        signals,
        purchases,
        new VoiceLibrary(null, "en", allowTesting: true),
        seed: 42)
{
    public const string Supporter = "fixture.supporter";

    public static new readonly CompanionAppConfiguration Configuration = new(
        StatusItemTooltip: "Fixture",
        AccessibilityLabel: "Fixture",
        Sounds: new CompanionSounds(Blip: "blip", Shh: "shh", Hello: "hello"),
        Status: FixtureCopy.Status("HH:mm"),
        Desktop: FixtureCopy.Desktop("HH:mm"),
        TuneBubbleText: () => "♪",
        Localize: (_, source) => source,
        SupporterProduct: Supporter);

    private readonly List<string> said = [];

    /// <summary>Every phrase about to be said, in order (the app's own state).</summary>
    public IReadOnlyList<string> Said => said;

    public override void WillSay(Phrase phrase) => said.Add(phrase.Id);

    public override void HandleNotificationAction(string id)
    {
        switch (id)
        {
            case "SAY_MORE":
                SayNow();
                break;
            case "SHH_HOUR":
                Shush(ChatterCadence.Shush.Hour);
                break;
        }
    }

    public override MenuCopy MenuCopy { get; } = FixtureCopy.Menu(accessKeys: false);

    public override DesktopLayout DesktopLayout { get; } = new(Fixture.Vector);

    public override RiggedCharacter GlyphCharacter { get; } = new(Fixture.Vector);
}

/// <summary>Ported from the Swift <c>CompanionAppModelTests</c>, case for case, plus the launch and host cases Windows needs.</summary>
public sealed class CompanionAppModelTests
{
    private static readonly ProductCatalog Catalog = new([FixtureModel.Supporter], ["fixture.tip"]);

    /// <summary>The fixture's single lines: no tunes, no jokes in beats, so every delivery is one spoken line.</summary>
    private static readonly PhraseBank Lines = new(Fixture.Bank.Phrases.Where(p => p.Kind is PhraseKind.Line));

    private MockClock clock = null!;
    private MockIdle idle = null!;
    private MockBusy busy = null!;
    private CompanionSettings settings = null!;
    private FakeSpeaker speaker = null!;
    private FakeChime chime = null!;
    private FakeNotifier notifier = null!;
    private FakeHost<FixtureWindow> host = null!;
    private ManualTimers timers = null!;

    /// <summary>Monday 2026-09-28 at the given UTC time: inside the default active hours.</summary>
    private static DateTimeOffset Monday(int hour, int minute = 0) => new(2026, 9, 28, hour, minute, 0, TimeSpan.Zero);

    private FixtureModel MakeModel(PhraseBank? bank = null, Action<CompanionSettings>? configure = null)
    {
        clock = new MockClock(Monday(10));
        idle = new MockIdle();
        busy = new MockBusy();
        settings = new CompanionSettings(new InMemorySettingsStore(), Fixture.Defaults) { HasCompletedOnboarding = true };
        configure?.Invoke(settings);
        speaker = new FakeSpeaker();
        chime = new FakeChime();
        notifier = new FakeNotifier();
        timers = new ManualTimers();
        host = new FakeHost<FixtureWindow>(timers);
        var signals = new SignalHub(idle, busy, null, null, null, null, timers);
        var purchases = new PurchaseStore(Catalog, new MockStoreBackend(Catalog), new InMemorySettingsStore());
        return new FixtureModel(settings, bank ?? Lines, host, clock, idle, busy, speaker, chime, notifier, signals, purchases);
    }

    /// <summary>Ticks once a minute for <paramref name="minutes"/>.</summary>
    private void Run(FixtureModel model, int minutes)
    {
        for (var i = 0; i < minutes; i++)
        {
            clock.AdvanceMinutes(1);
            model.Tick();
        }
    }

    private static string Text(string id) => Fixture.Bank[id]!.LocalizedText((_, source) => source)!;

    [Fact]
    public void PokeSpeaksWhenVoiceIsOn()
    {
        var model = MakeModel();
        model.SayNow();
        Assert.Single(speaker.Spoken);
        Assert.Equal(speaker.Spoken[0], model.Bubble);
        Assert.Equal(["button-press-1"], model.Said); // the app heard what is about to be said
        Assert.True(model.IsSpeaking);
        Assert.True(model.IsVoicing);
        Assert.Equal(Mood.Talking, model.Mood);
        speaker.Finish();
        Assert.False(model.IsSpeaking);
        Assert.False(model.IsVoicing);
        Assert.Equal(Mood.Awake, model.Mood);
        Assert.Empty(chime.Played); // the chime channel is off by default
    }

    [Fact]
    public void PokeOnlyShowsTheBubbleWhenEveryChannelIsOff()
    {
        var model = MakeModel(configure: s => s.Channels = Channels.Quiet);
        model.SayNow();
        Assert.NotNull(model.Bubble);
        Assert.Empty(speaker.Spoken);
        Assert.Empty(notifier.Posted);
    }

    [Fact]
    public void BlipPrecedesTheLineWhenTheChimeIsOn()
    {
        var model = MakeModel(configure: s => s.Channels = Channels.Voice | Channels.Chime);
        model.SayNow();
        Assert.Equal(["blip"], chime.Played);
        Assert.Single(speaker.Spoken);
    }

    [Fact]
    public void ATickOnACallDeliversNothing()
    {
        var model = MakeModel();
        busy.Reasons = [BusyReason.Call];
        Run(model, 75);
        Assert.Empty(speaker.Spoken);
        Assert.Null(model.Bubble);
        Assert.Equal(Mood.HushedForCall, model.Mood);
        Assert.Equal("Quiet: you're on a call", model.StatusLine);
    }

    [Fact]
    public void AmbientLinesArriveWhenCalm()
    {
        var model = MakeModel();
        Run(model, 75);
        Assert.NotEmpty(speaker.Spoken);
        Assert.StartsWith("Next in about", model.StatusLine);
    }

    [Fact]
    public void TurningOnTheNotificationChannelAsksForPermissionOnce()
    {
        var model = MakeModel();
        Assert.Equal(0, notifier.Requests);
        model.SetChannel(Channels.Chime, true);
        Assert.Equal(0, notifier.Requests);
        model.SetChannel(Channels.Notification, true);
        Assert.Equal(1, notifier.Requests);
        Assert.True(settings.Channels.Contains(Channels.Notification));
        Assert.Equal(NotificationPermission.Authorized, model.NotificationPermission);
        Run(model, 75);
        Assert.Equal(1, notifier.Requests); // ticks and deliveries never ask again
        Assert.NotEmpty(notifier.Posted); // the line went out as a notification
    }

    [Fact]
    public void TurningAChannelOffKeepsTheOthers()
    {
        var model = MakeModel(configure: s => s.Channels = Channels.All);
        model.SetChannel(Channels.Chime, false);
        Assert.Equal(Channels.Voice | Channels.Notification, settings.Channels);
    }

    [Fact]
    public void OnboardingAsksOnlyWhenNotificationsWerePicked()
    {
        var quiet = MakeModel(configure: s => s.HasCompletedOnboarding = false);
        quiet.CompleteOnboarding();
        Assert.Equal(0, notifier.Requests);
        Assert.True(settings.HasCompletedOnboarding);
        var picked = MakeModel(configure: s =>
        {
            s.HasCompletedOnboarding = false;
            s.Channels = Channels.Notification;
        });
        picked.CompleteOnboarding();
        Assert.Equal(1, notifier.Requests);
    }

    [Fact]
    public void ShhForAnHourSetsTheStatusLineAndNoSpeech()
    {
        var model = MakeModel(configure: s => s.Channels = Channels.Voice | Channels.Chime);
        model.Shush(ChatterCadence.Shush.Hour);
        Assert.True(model.IsShushed);
        Assert.Equal(Mood.Shushed, model.Mood);
        Assert.Equal(["shh"], chime.Played);
        Assert.Equal("Shh until 11:00", model.StatusLine);
        Assert.Equal(Monday(11), settings.ShushedUntil);
        Run(model, 59);
        Assert.Empty(speaker.Spoken);
    }

    [Fact]
    public void HoldingShushesForAnHourOnlyWhenAwake()
    {
        var model = MakeModel();
        Assert.True(model.HoldToShush());
        Assert.True(model.IsShushed);
        Assert.Equal(Mood.Shushed, model.Mood);
        Assert.Equal(Monday(11), settings.ShushedUntil);
        clock.AdvanceMinutes(10);
        Assert.False(model.HoldToShush()); // holding while shushed does nothing
        Assert.Equal(Monday(11), settings.ShushedUntil);

        var indefinite = MakeModel();
        indefinite.Shush(ChatterCadence.Shush.UntilISay);
        Assert.False(indefinite.HoldToShush()); // holding never shortens a longer Shh
        Assert.Equal("Shh until you say", indefinite.StatusLine);
    }

    [Fact]
    public void ShhRestOfTodayAndUntilISayHaveTheirOwnStatusLines()
    {
        var today = MakeModel();
        today.Shush(ChatterCadence.Shush.RestOfToday);
        Assert.Equal("Shh for the rest of today", today.StatusLine);
        var forever = MakeModel();
        forever.Shush(ChatterCadence.Shush.UntilISay);
        Assert.Equal("Shh until you say", forever.StatusLine);
    }

    [Fact]
    public void WakeSpeaksTheShushEndLine()
    {
        var model = MakeModel();
        model.Shush(ChatterCadence.Shush.UntilISay);
        Run(model, 120);
        Assert.Empty(speaker.Spoken);
        model.Wake();
        Assert.False(model.IsShushed);
        Assert.Equal([Text("shush-end-1")], speaker.Spoken);
        Assert.StartsWith("Next in about", model.StatusLine);
    }

    [Fact]
    public void AnUnknownPostponeReasonThrows() =>
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            FixtureModel.StatusLineFor(
                new SchedulerStatus.Postponed((BusyReason)99),
                Snapshot.Calm,
                DateTimeOffset.UnixEpoch,
                TimeZoneInfo.Utc,
                DateTimeOffset.UnixEpoch.AddHours(12),
                FixtureCopy.Status("HH:mm")
            )
        );

    [Fact]
    public void ACallStartingMidLineStopsSpeech()
    {
        var model = MakeModel();
        model.SayNow();
        Assert.True(model.IsSpeaking);
        busy.Reasons = [BusyReason.Call];
        model.Signals.PollBusy();
        Assert.Equal(1, speaker.Stops);
        Assert.False(model.IsSpeaking);
        Assert.False(model.IsVoicing);
    }

    [Fact]
    public void TheBusyWatchRunsWhileALineIsSpoken()
    {
        var model = MakeModel();
        model.SayNow();
        Assert.True(model.Signals.IsWatchingBusy);
        busy.Reasons = [BusyReason.Call];
        timers.Advance(1); // the hub's 1 s poll, not a hand-run poll
        Assert.False(model.IsSpeaking);
        Assert.False(model.Signals.IsWatchingBusy);
    }

    [Fact]
    public void ShhCutsTheCurrentLine()
    {
        var model = MakeModel();
        model.SayNow();
        model.Shush(ChatterCadence.Shush.Hour);
        Assert.Equal(1, speaker.Stops);
        Assert.False(model.IsSpeaking);
        Assert.Equal(Mood.Shushed, model.Mood);
    }

    [Fact]
    public void SayMoreMidLineCutsTheLineAndSaysAnother()
    {
        var model = MakeModel();
        model.SayNow();
        Assert.True(model.IsSpeaking);
        // A notification's Say more, pressed while its line is still heard.
        notifier.OnAction?.Invoke("SAY_MORE");
        Assert.Equal(1, speaker.Stops); // the line being said is cut
        Assert.Equal(2, speaker.Spoken.Count); // and the press is answered at once, not dropped
        Assert.Equal(2, model.Said.Count);
        Assert.Equal(speaker.Spoken[^1], model.Bubble);
        Assert.True(model.IsSpeaking);
        speaker.Finish();
        Assert.False(model.IsSpeaking);
    }

    [Fact]
    public void ResetSettingsEndsShhWithoutALine()
    {
        var model = MakeModel(configure: s => s.Frequency = Frequency.Chatty);
        model.Shush(ChatterCadence.Shush.UntilISay);
        model.ResetSettings();
        Assert.False(model.IsShushed);
        Assert.Equal(Frequency.Rarely, settings.Frequency);
        Assert.Empty(speaker.Spoken);
    }

    [Fact]
    public void TimingChangesReachTheSchedulerAfterAPause()
    {
        var model = MakeModel();
        settings.Frequency = Frequency.Chatty;
        settings.Frequency = Frequency.Sometimes;
        Assert.Equal([0.3], timers.Delays); // one debounce, restarted
        timers.RunNext();
        Assert.Empty(timers.Delays);
    }

    [Fact]
    public void TurningOnAChannelTilePreviewsItOnce()
    {
        var model = MakeModel();
        var hello = Fixture.Bank["general-a1"]!;
        model.PreviewChannel(Channels.Chime, hello);
        Assert.Equal(["blip"], chime.Played);
        model.PreviewChannel(Channels.Voice, hello);
        Assert.Equal([Text("general-a1")], speaker.Spoken);
        Assert.Equal([null], speaker.Clips); // no recorded voice: the system voice speaks
        Assert.Null(model.BubblePhraseId); // a preview can't be muted
        model.PreviewChannel(Channels.Notification, hello);
        Assert.Equal(["blip"], chime.Played); // a notification has nothing to hear
        Assert.Equal([Text("general-a1")], speaker.Spoken);
    }

    [Fact]
    public void ChannelPreviewsKeepTheirManners()
    {
        var model = MakeModel();
        var hello = Fixture.Bank["general-a1"]!;
        busy.Reasons = [BusyReason.Call];
        model.PreviewChannel(Channels.Chime, hello);
        model.PreviewChannel(Channels.Voice, hello);
        Assert.Empty(chime.Played); // never during a call
        Assert.Empty(speaker.Spoken); // never during a call
        busy.Reasons = [];
        model.PreviewChannel(Channels.Voice, hello);
        model.PreviewChannel(Channels.Voice, Fixture.Bank["general-b1"]!);
        Assert.Equal([Text("general-a1")], speaker.Spoken); // never over a line already playing
    }

    [Fact]
    public void DontSayThatAgainMutesTheBubbleLineWithUndo()
    {
        var model = MakeModel();
        model.SayNow();
        var id = Assert.IsType<string>(model.BubblePhraseId);
        model.MuteBubbleLine();
        Assert.Equal(new HashSet<string> { id }, settings.MutedPhraseIDs);
        Assert.Equal(id, model.JustMuted);
        model.UndoMute();
        Assert.Empty(settings.MutedPhraseIDs);
        Assert.Null(model.JustMuted);
    }

    [Fact]
    public void TheUndoGoesAwayAfterEightSeconds()
    {
        var model = MakeModel(configure: s => s.Channels = Channels.Quiet);
        model.SayNow();
        model.MuteBubbleLine();
        timers.Advance(7.9);
        Assert.NotNull(model.JustMuted);
        timers.Advance(0.2);
        Assert.Null(model.JustMuted);
    }

    [Fact]
    public void NotificationActionsRouteBack()
    {
        var model = MakeModel();
        notifier.OnAction?.Invoke("SAY_MORE");
        Assert.Single(speaker.Spoken);
        notifier.OnAction?.Invoke("SHH_HOUR");
        Assert.True(model.IsShushed);
    }

    [Fact]
    public void VoicingIsOffInTheBeatBetweenASetupAndItsPunchline()
    {
        var model = MakeModel(bank: new PhraseBank([Fixture.Bank["joke-1"]!]));
        var voicing = new List<bool>();
        model.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(model.IsVoicing))
                voicing.Add(model.IsVoicing);
        };
        model.SayNow();
        Assert.Equal(["Fixture joke, setup"], speaker.Spoken);
        Assert.True(model.IsVoicing);
        speaker.Finish();
        Assert.True(model.IsSpeaking); // the beat is part of the joke
        Assert.False(model.IsVoicing); // the mouth rests in the beat
        Assert.Equal("Fixture joke, setup", model.Bubble);
        timers.RunNext();
        Assert.Equal(["Fixture joke, setup", "Fixture joke, punchline"], speaker.Spoken);
        Assert.True(model.IsVoicing);
        Assert.Equal("Fixture joke, punchline", model.Bubble);
        speaker.Finish();
        Assert.False(model.IsSpeaking);
        Assert.False(model.IsVoicing);
        Assert.Equal([true, false, true, false], voicing);
    }

    // Desktop

    [Fact]
    public void TheDesktopCharacterIsOffUntilTurnedOn()
    {
        var model = MakeModel();
        model.RefreshStatus();
        Assert.False(model.IsOnDesktop);
        model.SetShowOnDesktop(true);
        Assert.True(model.IsOnDesktop);
        Assert.True(settings.ShowOnDesktop);
        model.SetShowOnDesktop(false);
        Assert.False(model.IsOnDesktop);
    }

    [Fact]
    public void ACallTakesTheCharacterOffTheDesktopUntilTenSecondsAfterIt()
    {
        var model = MakeModel(configure: s => s.ShowOnDesktop = true);
        model.RefreshStatus();
        Assert.True(model.IsOnDesktop);
        busy.Reasons = [BusyReason.Call];
        model.Signals.PollBusy();
        Assert.False(model.IsOnDesktop);
        // Still on the call a minute later: the presence keeps learning the moment from each poll.
        clock.AdvanceMinutes(1);
        model.Signals.PollBusy();
        busy.Reasons = [];
        clock.AdvanceSeconds(5);
        model.Signals.PollBusy();
        Assert.False(model.IsOnDesktop);
        clock.AdvanceSeconds(5);
        model.RefreshStatus();
        Assert.True(model.IsOnDesktop);
    }

    [Fact]
    public void ADeliveredLineFillsTheDesktopBubble()
    {
        var model = MakeModel(configure: s =>
        {
            s.ShowOnDesktop = true;
            s.Channels = Channels.Quiet;
        });
        model.SayNow();
        Assert.True(model.IsOnDesktop);
        Assert.NotNull(model.DesktopBubble);
        Assert.Equal(model.Bubble, model.DesktopBubble);
        Assert.NotNull(model.DesktopCountdown); // nothing spoken: the bubble runs down at once
        model.DismissDesktopBubble();
        Assert.Null(model.DesktopBubble);
        Assert.NotNull(model.Bubble); // the panel keeps its line
    }

    [Fact]
    public void TheDesktopBubbleRunsDownByItself()
    {
        var model = MakeModel(configure: s =>
        {
            s.ShowOnDesktop = true;
            s.Channels = Channels.Quiet;
        });
        model.SayNow();
        var countdown = Assert.IsType<BubbleCountdown>(model.DesktopCountdown);
        Assert.Equal(DesktopPresence.BubbleSeconds(model.Bubble!), countdown.Seconds);
        timers.Advance(countdown.Seconds);
        Assert.Null(model.DesktopBubble);
    }

    [Fact]
    public void ACallClearsTheDesktopBubble()
    {
        var model = MakeModel(configure: s =>
        {
            s.ShowOnDesktop = true;
            s.Channels = Channels.Quiet;
        });
        model.SayNow();
        busy.Reasons = [BusyReason.Call];
        model.RefreshStatus();
        Assert.Null(model.DesktopBubble);
    }

    [Fact]
    public void HideForAnHourComesBackAnHourLater()
    {
        var model = MakeModel(configure: s => s.ShowOnDesktop = true);
        model.HideDesktop(DesktopHide.Hour);
        Assert.False(model.IsOnDesktop);
        Assert.True(settings.ShowOnDesktop);
        Assert.Equal(Monday(11), model.DesktopHiddenUntil);
        Assert.Equal("Back at 11:00", model.DesktopBackLine);
        clock.AdvanceMinutes(59);
        model.RefreshStatus();
        Assert.False(model.IsOnDesktop);
        clock.AdvanceMinutes(1);
        model.RefreshStatus();
        Assert.True(model.IsOnDesktop);
        Assert.Null(model.DesktopHiddenUntil);
        Assert.Null(model.DesktopBackLine);
    }

    [Fact]
    public void ShowNowEndsAHideForTheRestOfToday()
    {
        var model = MakeModel(configure: s => s.ShowOnDesktop = true);
        model.HideDesktop(DesktopHide.RestOfToday);
        Assert.False(model.IsOnDesktop);
        Assert.Equal("Back tomorrow", model.DesktopBackLine);
        model.ShowDesktopNow();
        Assert.True(model.IsOnDesktop);
        Assert.Null(model.DesktopBackLine);
    }

    [Fact]
    public void HideUntilShownTurnsTheSettingOff()
    {
        var model = MakeModel(configure: s => s.ShowOnDesktop = true);
        model.HideDesktop(DesktopHide.UntilShown);
        Assert.False(settings.ShowOnDesktop);
        Assert.False(model.IsOnDesktop);
        Assert.Null(model.DesktopBackLine);
        model.ShowDesktopNow();
        Assert.True(settings.ShowOnDesktop);
        Assert.True(model.IsOnDesktop);
    }

    [Fact]
    public void APinchStepsTheSizeAndStopsAtTheEnds()
    {
        var model = MakeModel();
        model.StepDesktopSize(1);
        Assert.Equal(DesktopSize.Large, settings.DesktopSize);
        model.StepDesktopSize(1);
        Assert.Equal(DesktopSize.Large, settings.DesktopSize);
        model.StepDesktopSize(-1);
        model.StepDesktopSize(-1);
        model.StepDesktopSize(-1);
        Assert.Equal(DesktopSize.Small, settings.DesktopSize);
    }

    [Fact]
    public void ResetPutsTheCharacterBackInTheCorner()
    {
        var model = MakeModel(configure: s => s.ShowOnDesktop = true);
        model.MoveDesktop(new DesktopPosition(300, 400));
        model.HideDesktop(DesktopHide.Hour);
        model.ResetSettings();
        Assert.Null(settings.DesktopPosition);
        Assert.False(settings.ShowOnDesktop);
        Assert.False(model.IsOnDesktop);
        Assert.Null(model.DesktopHiddenUntil);
    }

    // Menus

    private static IReadOnlyList<string> Titles(IReadOnlyList<MenuItem> menu) => menu.Select(item => item.Title).ToArray();

    [Fact]
    public void TheMenusFollowTheState()
    {
        var model = MakeModel();
        Assert.Equal(["Say something", "Shh", "", "On the desktop", "", "Settings…", "Support…", "", "Quit"], Titles(model.MenuBarMenu()));
        Assert.Equal(["Say something", "Shh", "", "Size", "Hide", "", "Settings…", "Support…", "", "Quit"], Titles(model.DesktopMenu()));
        model.Shush(ChatterCadence.Shush.Hour);
        Assert.Equal("Wake up", Titles(model.MenuBarMenu())[1]);
        var desktop = Assert.IsType<MenuItem.Submenu>(model.MenuBarMenu()[3]).Items;
        Assert.Equal(["Show", "Hide for an hour", "Hide for the rest of today", "", "small", "medium", "large"], Titles(desktop));
        Assert.False(Assert.IsType<MenuItem.Command>(desktop[0]).IsChecked);
        Assert.False(Assert.IsType<MenuItem.Command>(desktop[1]).IsEnabled); // nothing to hide while it's off
        Assert.True(Assert.IsType<MenuItem.Command>(desktop[5]).IsChecked); // medium is the default size
    }

    [Fact]
    public void MenuCommandsAct()
    {
        var model = MakeModel();
        var shh = Assert.IsType<MenuItem.Submenu>(model.MenuBarMenu()[1]);
        Assert.IsType<MenuItem.Command>(shh.Items[0]).Invoke();
        Assert.True(model.IsShushed);
        Assert.IsType<MenuItem.Command>(model.MenuBarMenu()[1]).Invoke(); // Wake up
        Assert.False(model.IsShushed);
        Assert.IsType<MenuItem.Command>(model.MenuBarMenu()[^1]).Invoke();
        Assert.Equal(1, host.Quits);
    }

    // Launch (the Swift tests never launch: AppKit would open windows)

    [Fact]
    public void LaunchMakesTheStatusItemAndTicksEveryTwentySeconds()
    {
        var model = MakeModel();
        model.Launch(showHome: true);
        Assert.NotNull(host.StatusItem);
        Assert.Equal(Mood.Awake, host.StatusItem!.Shown?.Mood);
        Assert.Equal([FixtureWindow.Home], host.WindowHost.Open);
        Assert.Equal(AppearanceSetting.System, host.Appearance);
        Assert.Equal(1, timers.RepeatingCount);
        model.SayNow();
        Assert.Single(speaker.Spoken);
        Assert.True(host.StatusItem.Shown?.Talking);
    }

    [Fact]
    public void ASignInLaunchShowsNothingAndTheFirstRunShowsOnboarding()
    {
        var login = MakeModel();
        login.Launch(showHome: false);
        Assert.Empty(host.WindowHost.Open);
        var first = MakeModel(configure: s => s.HasCompletedOnboarding = false);
        first.Launch(showHome: true);
        Assert.Equal([FixtureWindow.Onboarding], host.WindowHost.Open);
        first.CompleteOnboarding();
        Assert.Equal([FixtureWindow.Home], host.WindowHost.Open);
    }

    [Fact]
    public void TheDesktopWindowFollowsTheModelOnceLaunched()
    {
        var model = MakeModel(configure: s =>
        {
            s.ShowOnDesktop = true;
            s.Channels = Channels.Quiet;
        });
        model.Launch(showHome: false);
        var desktop = Assert.IsType<FakeDesktopCompanion>(host.Desktop);
        Assert.True(desktop.IsShown);
        Assert.Equal(DesktopSize.Medium, desktop.Size);
        desktop.OnSay?.Invoke();
        Assert.Equal(model.Bubble, desktop.Bubble);
        desktop.OnPinch?.Invoke(1);
        Assert.Equal(DesktopSize.Large, desktop.Size);
        desktop.OnMoved?.Invoke(new DesktopPosition(10, 20));
        Assert.Equal(new DesktopPosition(10, 20), desktop.Position);
        Assert.Equal("Size", desktop.Menu?.Invoke()[3].Title);
        busy.Reasons = [BusyReason.Call];
        timers.Advance(1); // the desktop's busy watch
        Assert.False(desktop.IsShown);
        busy.Reasons = [];
        clock.AdvanceSeconds(10);
        timers.Advance(1); // the poll sees the change; the recheck brings it back
        timers.Advance(DesktopPresence.ReappearDelaySeconds);
        Assert.True(desktop.IsShown);
    }

    [Fact]
    public async Task ComputedStateAnnouncesItsChanges()
    {
        var model = MakeModel(configure: s => s.ShowOnDesktop = true);
        var changed = new List<string?>();
        model.PropertyChanged += (_, e) => changed.Add(e.PropertyName);
        model.HideDesktop(DesktopHide.Hour);
        Assert.Contains(nameof(model.DesktopBackLine), changed); // the panel row says when he's back
        changed.Clear();
        settings.ShowOnDesktop = false;
        Assert.Contains(nameof(model.DesktopBackLine), changed);
        changed.Clear();
        await model.Purchases.BuyAsync(FixtureModel.Supporter);
        Assert.True(model.IsSupporter);
        Assert.Contains(nameof(model.IsSupporter), changed); // the Support row thanks instead of asking
    }

    [Fact]
    public async Task ClosingSupportAcknowledgesATip()
    {
        var model = MakeModel();
        model.OpenSupport();
        await model.Purchases.TipAsync("fixture.tip");
        Assert.True(model.Purchases.JustTipped);
        model.CloseWindow(FixtureWindow.Support);
        Assert.False(host.WindowHost.IsOpen(FixtureWindow.Support));
        Assert.False(model.Purchases.JustTipped); // the thanks doesn't wait for the next visit
    }
}
