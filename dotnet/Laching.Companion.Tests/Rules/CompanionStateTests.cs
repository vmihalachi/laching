using Laching.Core.Scheduling;
using Laching.Companion.Rules;

namespace Laching.Companion.Tests;

/// <summary>One test per <see cref="Mood"/>, naming the status and snapshot that produce it, then the order the moods win in.</summary>
public sealed class CompanionStateTests
{
    private static readonly SchedulerStatus Scheduled = new SchedulerStatus.Scheduled(DateTimeOffset.UnixEpoch);

    private static readonly SchedulerStatus Paused = new SchedulerStatus.Paused(DateTimeOffset.UnixEpoch.AddMinutes(10));

    private static Mood MoodOf(SchedulerStatus status, Snapshot? snapshot = null) =>
        CompanionState.MoodFor(status, snapshot ?? Snapshot.Calm);

    private static Snapshot BusyWith(params BusyReason[] reasons) => new() { Busy = reasons.ToHashSet() };

    [Fact]
    public void AwakeIsScheduledWithACalmSnapshot() => Assert.Equal(Mood.Awake, MoodOf(Scheduled));

    [Fact]
    public void TalkingIsSpeaking() => Assert.Equal(Mood.Talking, MoodOf(Scheduled, new Snapshot { IsSpeaking = true }));

    [Fact]
    public void ShushedIsPaused() => Assert.Equal(Mood.Shushed, MoodOf(Paused));

    [Fact]
    public void AsleepIsALockedScreenIdleAtTheThresholdOrAway()
    {
        Assert.Equal(Mood.Asleep, MoodOf(Scheduled, new Snapshot { IsScreenLocked = true }));
        Assert.Equal(Mood.Asleep, MoodOf(Scheduled, new Snapshot { IdleSeconds = QuietRules.AwayThresholdSeconds }));
        Assert.Equal(Mood.Awake, MoodOf(Scheduled, new Snapshot { IdleSeconds = QuietRules.AwayThresholdSeconds - 1 }));
        Assert.Equal(Mood.Asleep, MoodOf(new SchedulerStatus.Away()));
    }

    [Fact]
    public void HushedForCallIsABusyCallOrAPostponedOne()
    {
        Assert.Equal(Mood.HushedForCall, MoodOf(Scheduled, BusyWith(BusyReason.Call)));
        Assert.Equal(Mood.HushedForCall, MoodOf(new SchedulerStatus.Postponed(BusyReason.Call)));
    }

    [Fact]
    public void HushedFullScreenIsABusyFullScreenOrAPostponedOne()
    {
        Assert.Equal(Mood.HushedFullScreen, MoodOf(Scheduled, BusyWith(BusyReason.FullScreen)));
        Assert.Equal(Mood.HushedFullScreen, MoodOf(new SchedulerStatus.Postponed(BusyReason.FullScreen)));
    }

    [Fact]
    public void OffHoursIsOutsideActiveHours() => Assert.Equal(Mood.OffHours, MoodOf(new SchedulerStatus.OutsideActiveHours()));

    /// <summary>A new mood needs a test above.</summary>
    [Fact]
    public void TheMoodsAreTheSevenAbove() =>
        Assert.Equal(
            [Mood.Awake, Mood.Talking, Mood.Shushed, Mood.Asleep, Mood.HushedForCall, Mood.HushedFullScreen, Mood.OffHours],
            Moods.All);

    // Precedence.

    [Fact]
    public void TalkingWinsOverEverything()
    {
        var everything = new Snapshot
        {
            Busy = new HashSet<BusyReason> { BusyReason.Call, BusyReason.FullScreen },
            IdleSeconds = QuietRules.AwayThresholdSeconds,
            IsScreenLocked = true,
            IsSpeaking = true,
        };
        Assert.Equal(Mood.Talking, MoodOf(Paused, everything));
        Assert.Equal(Mood.Talking, MoodOf(new SchedulerStatus.OutsideActiveHours(), everything));
    }

    [Fact]
    public void PausedWinsOverACall() => Assert.Equal(Mood.Shushed, MoodOf(Paused, BusyWith(BusyReason.Call)));

    [Fact]
    public void ACallWinsOverFullScreen() =>
        Assert.Equal(Mood.HushedForCall, MoodOf(Scheduled, BusyWith(BusyReason.Call, BusyReason.FullScreen)));

    [Fact]
    public void ALockedScreenWinsOverAPostponedStatus()
    {
        Assert.Equal(Mood.Asleep, MoodOf(new SchedulerStatus.Postponed(BusyReason.Call), new Snapshot { IsScreenLocked = true }));
        Assert.Equal(
            Mood.Asleep,
            MoodOf(new SchedulerStatus.Postponed(BusyReason.FullScreen), new Snapshot { IdleSeconds = QuietRules.AwayThresholdSeconds }));
    }

    [Fact]
    public void OffHoursComesLastBeforeAwake()
    {
        var offHours = new SchedulerStatus.OutsideActiveHours();
        Assert.Equal(Mood.HushedFullScreen, MoodOf(offHours, BusyWith(BusyReason.FullScreen)));
        Assert.Equal(Mood.Asleep, MoodOf(offHours, new Snapshot { IsScreenLocked = true }));
        Assert.Equal(Mood.OffHours, MoodOf(offHours));
    }

    [Fact]
    public void RawValuesRoundTrip()
    {
        foreach (var mood in Moods.All)
            Assert.Equal(mood, Moods.Parse(mood.RawValue()));
        Assert.Equal("hushedFullScreen", Mood.HushedFullScreen.RawValue());
        Assert.Null(Moods.Parse("sleepy"));
    }

    [Fact]
    public void AnUnknownMoodThrows() => Assert.Throws<ArgumentOutOfRangeException>(() => ((Mood)99).RawValue());
}
