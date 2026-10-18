using Laching.Core.Scheduling;
using Laching.Companion.Phrases;
using Laching.Companion.Rules;

namespace Laching.Companion.Tests;

public sealed class QuietRulesTests
{
    private static readonly Manners Talkative = new(Channels.Voice | Channels.Chime);

    private static Snapshot BusyWith(params BusyReason[] reasons) => new() { Busy = reasons.ToHashSet() };

    [Fact]
    public void CalmMomentSpeaksAndChimes()
    {
        Assert.Equal(new Outcome.Deliver(new ChannelPlan(true, true, false)), QuietRules.Decide(Trigger.Due, Snapshot.Calm, Talkative));
    }

    [Fact]
    public void BusyHoldsEverythingExceptTheButton()
    {
        var busy = BusyWith(BusyReason.Call);
        Assert.Equal(new Outcome.Skip(SkipReason.Busy), QuietRules.Decide(Trigger.Due, busy, Talkative));
        Assert.Equal(new Outcome.Skip(SkipReason.Busy), QuietRules.Decide(Trigger.WelcomeBack, busy, Talkative));
        Assert.Equal(new Outcome.Deliver(ChannelPlan.BubbleOnly), QuietRules.Decide(Trigger.ButtonPress, busy, Talkative));
    }

    [Fact]
    public void AwayAndLockedSayNothing()
    {
        var away = new Snapshot { IdleSeconds = 600 };
        var locked = new Snapshot { IsScreenLocked = true };
        Assert.Equal(new Outcome.Skip(SkipReason.Away), QuietRules.Decide(Trigger.Due, away, Talkative));
        Assert.Equal(new Outcome.Skip(SkipReason.Locked), QuietRules.Decide(Trigger.Due, locked, Talkative));
        // Coming back is the one thing worth saying to someone who was away.
        Assert.Equal(new Outcome.Deliver(new ChannelPlan(true, true, false)), QuietRules.Decide(Trigger.WelcomeBack, away, Talkative));
    }

    [Fact]
    public void NothingOverlapsSpeech()
    {
        var speaking = new Snapshot { IsSpeaking = true };
        Assert.Equal(new Outcome.Skip(SkipReason.Speaking), QuietRules.Decide(Trigger.Due, speaking, Talkative));
        Assert.Equal(new Outcome.Skip(SkipReason.Speaking), QuietRules.Decide(Trigger.WelcomeBack, speaking, Talkative));
    }

    [Fact]
    public void AButtonPressAnswersMidLineAndTheShellCutsIt()
    {
        // A notification's Say more comes while its line is still heard: the press is delivered, never queued.
        var speaking = new Snapshot { IsSpeaking = true };
        Assert.Equal(new Outcome.Deliver(new ChannelPlan(true, true, false)), QuietRules.Decide(Trigger.ButtonPress, speaking, Talkative));
        Assert.Equal(
            new Outcome.Deliver(ChannelPlan.BubbleOnly),
            QuietRules.Decide(Trigger.ButtonPress, BusyWith(BusyReason.Call) with { IsSpeaking = true }, Talkative));
        Assert.Equal(
            new Outcome.Skip(SkipReason.Locked),
            QuietRules.Decide(Trigger.ButtonPress, new Snapshot { IsScreenLocked = true, IsSpeaking = true }, Talkative));
    }

    [Fact]
    public void MutedOutputFallsBackToANotificationWhenAllowed()
    {
        var muted = new Snapshot { IsOutputMuted = true, NotificationsAuthorized = true };
        Assert.Equal(new Outcome.Deliver(new ChannelPlan(false, false, true)), QuietRules.Decide(Trigger.Due, muted, Talkative));
        var notAllowed = new Snapshot { IsOutputMuted = true, NotificationsAuthorized = false };
        Assert.Equal(new Outcome.Deliver(ChannelPlan.BubbleOnly), QuietRules.Decide(Trigger.Due, notAllowed, Talkative));
    }

    [Fact]
    public void NotificationChannel()
    {
        var manners = new Manners(Channels.Notification);
        var banner = new Outcome.Deliver(new ChannelPlan(false, false, true));
        Assert.Equal(banner, QuietRules.Decide(Trigger.Due, Snapshot.Calm, manners));
        // A button press shows in the panel; no banner on top.
        Assert.Equal(new Outcome.Deliver(ChannelPlan.BubbleOnly), QuietRules.Decide(Trigger.ButtonPress, Snapshot.Calm, manners));
    }

    [Fact]
    public void QuietChannelsStillShowTheBubble()
    {
        var quiet = new Manners(Channels.Quiet);
        Assert.Equal(new Outcome.Deliver(ChannelPlan.BubbleOnly), QuietRules.Decide(Trigger.Due, Snapshot.Calm, quiet));
    }

    [Fact]
    public void DisabledComments()
    {
        var manners = new Manners(Channels.Voice, GreetOnReturn: false, CommentOnPower: false, CommentOnAppearance: false);
        var disabled = new Outcome.Skip(SkipReason.Disabled);
        Assert.Equal(disabled, QuietRules.Decide(Trigger.WelcomeBack, Snapshot.Calm, manners));
        Assert.Equal(disabled, QuietRules.Decide(Trigger.BatteryLow, Snapshot.Calm, manners));
        Assert.Equal(disabled, QuietRules.Decide(Trigger.Charging, Snapshot.Calm, manners));
        Assert.Equal(disabled, QuietRules.Decide(Trigger.WentDark, Snapshot.Calm, manners));
        Assert.Equal(new Outcome.Deliver(new ChannelPlan(true, false, false)), QuietRules.Decide(Trigger.Due, Snapshot.Calm, manners));
    }

    [Fact]
    public void TriggerTags()
    {
        Assert.Null(Trigger.Due.PhraseTag());
        Assert.Equal(Tag.AfterCall, Trigger.AfterCall.PhraseTag());
        Assert.Equal(Tag.ButtonPress, Trigger.ButtonPress.PhraseTag());
        foreach (var trigger in Triggers.All)
            Assert.Equal(trigger, Triggers.Parse(trigger.RawValue()));
    }

    [Fact]
    public void ChannelNamesRoundTrip()
    {
        Assert.Equal(new[] { "voice", "chime", "notification" }, Channels.All.Names.ToArray());
        Assert.Equal(Channels.Voice | Channels.Notification, Channels.FromNames(new[] { "notification", "voice" }));
        Assert.Equal(Channels.Quiet, Channels.FromNames(new[] { "jingle" }));
        Assert.Empty(Channels.Quiet.Names);
        Assert.True(Channels.Quiet.IsEmpty);
        var hasChime = Channels.All.Contains(Channels.Chime);
        var voiceHasChime = Channels.Voice.Contains(Channels.Chime);
        Assert.True(hasChime);
        Assert.False(voiceHasChime);
    }

    [Fact]
    public void SnapshotsCompareByValue()
    {
        Assert.Equal(BusyWith(BusyReason.Call, BusyReason.FullScreen), BusyWith(BusyReason.FullScreen, BusyReason.Call));
        Assert.NotEqual(Snapshot.Calm, new Snapshot { IsOutputMuted = false });
        Assert.Equal("busy", SkipReason.Busy.RawValue());
    }

    [Fact]
    public void AnUnknownTriggerOrSkipReasonThrows()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => ((Trigger)99).PhraseTag());
        Assert.Throws<ArgumentOutOfRangeException>(() => ((Trigger)99).RawValue());
        Assert.Throws<ArgumentOutOfRangeException>(() => ((SkipReason)99).RawValue());
    }
}
