using Laching.Core.Random;
using Laching.Core.Scheduling;

namespace Laching.Core.Tests;

public sealed class ReminderSchedulerTests
{
    private MockClock clock = new(Monday(10));
    private MockIdle idle = new();
    private MockBusy busy = new();

    [Fact]
    public void TestMondayFixtureIsAWeekday()
    {
        Assert.Equal(2, (int)Monday(10).DayOfWeek + 1);
    }

    [Fact]
    public void TestRemindsAfterInterval()
    {
        var scheduler = MakeScheduler(Monday(9, 30));
        Assert.Equal(Monday(10, 15), scheduler.NextFireDate);
        Assert.Equal([45, 90], Run(scheduler, 100));
    }

    [Fact]
    public void TestCustomInterval()
    {
        var scheduler = MakeScheduler(Monday(10), new SchedulerConfig { IntervalSeconds = 20 * 60 });
        Assert.Equal([20, 40, 60], Run(scheduler, 60));
    }

    [Fact]
    public void TestIdleUserIsNotRemindedAndTimerResets()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 30);
        idle.Idle = 10 * 60; // user walked away
        Assert.Empty(Run(scheduler, 30));
        Assert.IsType<SchedulerStatus.Away>(scheduler.Status);
        idle.Idle = 0; // back at the desk: a fresh full interval starts
        Assert.Equal([45], Run(scheduler, 50));
    }

    [Fact]
    public void TestShortIdleDoesNotCountAsAway()
    {
        var scheduler = MakeScheduler(Monday(10));
        idle.Idle = 2 * 60;
        Assert.Equal([45], Run(scheduler, 45));
    }

    [Fact]
    public void TestNoRemindersOutsideActiveHours()
    {
        var scheduler = MakeScheduler(Monday(17, 30));
        Assert.Empty(Run(scheduler, 29));
        clock.AdvanceMinutes(1);
        Assert.Equal(SchedulerDecision.None, scheduler.Tick()); // 18:00 – window closed
        Assert.IsType<SchedulerStatus.OutsideActiveHours>(scheduler.Status);
        Assert.Null(scheduler.NextFireDate);
        Assert.Empty(Run(scheduler, 300));
    }

    [Fact]
    public void TestEnteringActiveHoursStartsFreshInterval()
    {
        var scheduler = MakeScheduler(Monday(8));
        Assert.Null(scheduler.NextFireDate);
        // 9:00 enters the window; first reminder at 9:45.
        Assert.Equal([105], Run(scheduler, 110));
    }

    [Fact]
    public void TestNoRemindersOnWeekend()
    {
        var saturday = new DateTimeOffset(2026, 9, 26, 10, 0, 0, TimeSpan.Zero);
        Assert.Equal(7, (int)saturday.DayOfWeek + 1);
        var scheduler = MakeScheduler(saturday);
        Assert.Empty(Run(scheduler, 200));
    }

    [Fact]
    public void TestOvernightActiveWindow()
    {
        var config = new SchedulerConfig
        {
            ActiveStartMinute = 22 * 60,
            ActiveEndMinute = 6 * 60,
            ActiveWeekdays = Enumerable.Range(1, 7).ToHashSet(),
        };
        var scheduler = MakeScheduler(Monday(12), config);
        Assert.False(scheduler.IsWithinActiveHours(Monday(12)));
        Assert.True(scheduler.IsWithinActiveHours(Monday(23)));
        Assert.True(scheduler.IsWithinActiveHours(Monday(2)));
    }

    [Fact]
    public void TestSnoozeDelaysByTenMinutes()
    {
        var scheduler = MakeScheduler(Monday(10));
        Assert.Equal([45], Run(scheduler, 45));
        scheduler.Snooze(10);
        Assert.Equal([10], Run(scheduler, 15));
    }

    [Fact]
    public void TestSnoozeForSecondsRemindsAfterThatLong()
    {
        var scheduler = MakeScheduler(Monday(10));
        Assert.Equal([45], Run(scheduler, 45));
        scheduler.SnoozeFor(90);
        Assert.Equal(Monday(10, 46).AddSeconds(30), scheduler.NextFireDate);
        Assert.Equal(
            [SchedulerDecision.None, SchedulerDecision.None, SchedulerDecision.None, SchedulerDecision.None,
                SchedulerDecision.Remind, SchedulerDecision.None],
            Tick(scheduler, 120));
    }

    [Fact]
    public void TestSnoozeMinutesIsSnoozeForSixtyTimesAsManySeconds()
    {
        var s = MakeScheduler(Monday(10));
        _ = Run(s, 45);
        s.Snooze(10);
        var byMinutes = s.NextFireDate;
        var t = MakeScheduler(Monday(10));
        _ = Run(t, 45);
        t.SnoozeFor(600);
        Assert.Equal(byMinutes, t.NextFireDate);
    }

    [Fact]
    public void TestSnoozeWithoutReminderNeverBringsItCloser()
    {
        var scheduler = MakeScheduler(Monday(10));
        Assert.Empty(Run(scheduler, 20));
        scheduler.Snooze(10);
        // Still due at 10:45, not 10:30.
        Assert.Equal([25], Run(scheduler, 30));
    }

    [Fact]
    public void TestSnoozeAfterAnsweredReminderNeverBringsItCloser()
    {
        var scheduler = MakeScheduler(Monday(10));
        Assert.Equal([45], Run(scheduler, 45));
        scheduler.SessionCompleted();
        scheduler.Snooze(10);
        Assert.Equal([45], Run(scheduler, 45));
    }

    [Fact]
    public void TestSnoozeHeldReminderRemindsInTenMinutes()
    {
        var scheduler = MakeScheduler(Monday(10));
        busy.Reasons = [BusyReason.Call];
        Assert.Empty(Run(scheduler, 50));
        scheduler.Snooze(10);
        busy.Reasons = [];
        Assert.Equal([10], Run(scheduler, 10));
    }

    [Fact]
    public void TestSnoozeLaterThanNextReminderPushesItOut()
    {
        var scheduler = MakeScheduler(Monday(10));
        Assert.Empty(Run(scheduler, 40));
        scheduler.Snooze(10);
        Assert.Equal([10], Run(scheduler, 10));
    }

    [Fact]
    public void TestPauseSuppressesRemindersThenRestartsInterval()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.PauseFor(3600);
        Assert.IsType<SchedulerStatus.Paused>(scheduler.Status);
        Assert.Empty(Run(scheduler, 60));
        // Pause expired at +60; next reminder 45 min after that.
        Assert.Equal([45], Run(scheduler, 50));
    }

    [Fact]
    public void TestResumeEndsPauseEarly()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.PauseFor(3600);
        _ = Run(scheduler, 10);
        scheduler.Resume();
        Assert.Equal([45], Run(scheduler, 45));
    }

    [Fact]
    public void TestPauseUntilDateLastsAcrossDaysThenRestartsInterval()
    {
        var scheduler = MakeScheduler(Monday(10));
        var wednesday = new DateTimeOffset(2026, 9, 30, 0, 0, 0, TimeSpan.Zero);
        scheduler.PauseUntil(wednesday);
        Assert.Equal(new SchedulerStatus.Paused(wednesday), scheduler.Status);
        clock.Now = Monday(10).AddHours(30);
        Assert.Equal(SchedulerDecision.None, scheduler.Tick());
        clock.Now = new DateTimeOffset(2026, 9, 30, 10, 0, 0, TimeSpan.Zero);
        Assert.Equal(SchedulerDecision.None, scheduler.Tick());
        Assert.Null(scheduler.PausedUntil);
        Assert.Equal([45], Run(scheduler, 50));
    }

    [Fact]
    public void TestEndOfTodayIsNextMidnight()
    {
        var scheduler = MakeScheduler(Monday(23, 30));
        Assert.Equal(Monday(23, 30).AddMinutes(30), scheduler.EndOfToday);
        scheduler.PauseUntil(scheduler.EndOfToday);
        clock.Now = Monday(23, 59);
        Assert.Equal(SchedulerDecision.None, scheduler.Tick());
        Assert.IsType<SchedulerStatus.Paused>(scheduler.Status);
    }

    [Fact]
    public void TestEndOfTodayFollowsTheTimeZone()
    {
        var bucharest = TimeZoneInfo.FindSystemTimeZoneById("Europe/Bucharest");
        clock = new MockClock(Monday(22, 30)); // 01:30 on Tuesday in Bucharest (UTC+3)
        var scheduler = new ReminderScheduler(new SchedulerConfig(), clock, new MockIdle(), new MockBusy(), bucharest);
        Assert.Equal(new DateTimeOffset(2026, 9, 30, 0, 0, 0, TimeSpan.FromHours(3)), scheduler.EndOfToday);
    }

    [Fact]
    public void TestSessionCompletedResetsInterval()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 40);
        scheduler.SessionCompleted();
        Assert.Equal([45], Run(scheduler, 50));
    }

    [Fact]
    public void TestChangingIntervalReschedules()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 10);
        scheduler.Update(new SchedulerConfig { IntervalSeconds = 15 * 60 });
        Assert.Equal([15], Run(scheduler, 16));
    }

    // Busy

    [Fact]
    public void TestCallPostponesDueReminderAndFiresAfterGrace()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 40);
        busy.Reasons = [BusyReason.Call];
        Assert.Empty(Run(scheduler, 20)); // due at 45, held
        Assert.Equal(new SchedulerStatus.Postponed(BusyReason.Call), scheduler.Status);
        Assert.Equal(SchedulerDecision.Postpone, scheduler.Tick());
        busy.Reasons = [];
        // Last busy tick at t; +20 s is inside the 30 s grace, +40 s fires.
        Assert.Equal([SchedulerDecision.Postpone, SchedulerDecision.Remind], Tick(scheduler, 40));
        Assert.IsType<SchedulerStatus.Scheduled>(scheduler.Status);
        // Then a full interval from the release.
        Assert.Equal([45], Run(scheduler, 46));
    }

    [Fact]
    public void TestBusyBeforeDueDoesNotChangeTheSchedule()
    {
        var scheduler = MakeScheduler(Monday(10));
        busy.Reasons = [BusyReason.FullScreen];
        Assert.Empty(Run(scheduler, 30));
        Assert.IsType<SchedulerStatus.Scheduled>(scheduler.Status);
        busy.Reasons = [];
        Assert.Equal([15], Run(scheduler, 15));
    }

    [Fact]
    public void TestReturnsPostponeOnlyWhenAReminderIsHeld()
    {
        var scheduler = MakeScheduler(Monday(10));
        busy.Reasons = [BusyReason.Call];
        clock.AdvanceMinutes(10);
        Assert.Equal(SchedulerDecision.None, scheduler.Tick());
        clock.AdvanceMinutes(40);
        Assert.Equal(SchedulerDecision.Postpone, scheduler.Tick());
    }

    [Fact]
    public void TestDisabledReasonsAreIgnored()
    {
        var config = new SchedulerConfig { PostponeDuringFullScreen = false };
        var scheduler = MakeScheduler(Monday(10), config);
        busy.Reasons = [BusyReason.FullScreen];
        Assert.Equal([45], Run(scheduler, 45));
        scheduler.Update(config with { PostponeDuringCalls = false });
        busy.Reasons = [BusyReason.Call];
        Assert.Equal([45], Run(scheduler, 45));
    }

    [Fact]
    public void TestCallStatusWinsOverFullScreen()
    {
        var scheduler = MakeScheduler(Monday(10));
        busy.Reasons = [BusyReason.Call, BusyReason.FullScreen];
        _ = Run(scheduler, 46);
        Assert.Equal(new SchedulerStatus.Postponed(BusyReason.Call), scheduler.Status);
    }

    [Fact]
    public void TestIdleDuringCallIsNotABreak()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 30);
        busy.Reasons = [BusyReason.Call];
        idle.Idle = 20 * 60; // listening, hands off the keyboard
        Assert.Empty(Run(scheduler, 30));
        Assert.IsNotType<SchedulerStatus.Away>(scheduler.Status);
        busy.Reasons = [];
        idle.Idle = 0;
        Assert.Equal([1], Run(scheduler, 1)); // held reminder, not a reset
    }

    [Fact]
    public void TestWalkingAwayAfterCallDropsHeldReminder()
    {
        var scheduler = MakeScheduler(Monday(10));
        busy.Reasons = [BusyReason.Call];
        _ = Run(scheduler, 50);
        busy.Reasons = [];
        idle.Idle = 10 * 60;
        Assert.Empty(Run(scheduler, 5));
        Assert.IsType<SchedulerStatus.Away>(scheduler.Status);
        idle.Idle = 0;
        Assert.Equal([45], Run(scheduler, 46));
    }

    [Fact]
    public void TestHeldReminderIsDroppedWhenActiveHoursEnd()
    {
        var scheduler = MakeScheduler(Monday(17));
        busy.Reasons = [BusyReason.Call];
        _ = Run(scheduler, 65); // due 17:45, held; window closes 18:00
        Assert.IsType<SchedulerStatus.OutsideActiveHours>(scheduler.Status);
        busy.Reasons = [];
        Assert.Empty(Run(scheduler, 120));
        Assert.False(scheduler.IsHolding);
    }

    [Fact]
    public void TestBusyOutsideActiveHoursDoesNothing()
    {
        var scheduler = MakeScheduler(Monday(19));
        busy.Reasons = [BusyReason.Call];
        Assert.Equal(SchedulerDecision.None, scheduler.Tick());
        Assert.IsType<SchedulerStatus.OutsideActiveHours>(scheduler.Status);
    }

    [Fact]
    public void TestPauseWinsOverBusy()
    {
        var scheduler = MakeScheduler(Monday(10));
        busy.Reasons = [BusyReason.Call];
        _ = Run(scheduler, 50);
        scheduler.PauseFor(3600);
        busy.Reasons = [];
        Assert.Empty(Run(scheduler, 59));
        Assert.IsType<SchedulerStatus.Paused>(scheduler.Status);
    }

    [Fact]
    public void TestIsInCallReadsTheProvider()
    {
        var scheduler = MakeScheduler(Monday(10));
        Assert.False(scheduler.IsInCall);
        busy.Reasons = [BusyReason.Call];
        Assert.True(scheduler.IsInCall);
        busy.Reasons = [BusyReason.FullScreen];
        Assert.False(scheduler.IsInCall);
    }

    // After this call

    [Fact]
    public void TestAfterCallFiresWhenCallEnds()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 45);
        scheduler.SnoozeUntilAfterCall();
        Assert.True(scheduler.IsWaitingForCall);
        Assert.Empty(Run(scheduler, 2));
        busy.Reasons = [BusyReason.Call];
        Assert.Empty(Run(scheduler, 3));
        Assert.Equal(new SchedulerStatus.Postponed(BusyReason.Call), scheduler.Status);
        busy.Reasons = [];
        Assert.Equal([1], Run(scheduler, 1));
        Assert.False(scheduler.IsWaitingForCall);
    }

    [Fact]
    public void TestAfterCallWaitsForLongCallsPastTheFallback()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.SnoozeUntilAfterCall();
        busy.Reasons = [BusyReason.Call];
        Assert.Empty(Run(scheduler, 40));
        busy.Reasons = [];
        Assert.Equal([1], Run(scheduler, 1));
    }

    [Fact]
    public void TestAfterCallFallsBackWhenNoCallStarts()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.SnoozeUntilAfterCall();
        Assert.Equal([15], Run(scheduler, 20));
    }

    [Fact]
    public void TestAfterCallIgnoresFullScreen()
    {
        var scheduler = MakeScheduler(Monday(10), new SchedulerConfig { PostponeDuringFullScreen = false });
        scheduler.SnoozeUntilAfterCall();
        busy.Reasons = [BusyReason.FullScreen];
        Assert.Empty(Run(scheduler, 5));
        busy.Reasons = [];
        Assert.Empty(Run(scheduler, 5)); // still waiting for a call
        Assert.True(scheduler.IsWaitingForCall);
    }

    [Fact]
    public void TestAfterCallWorksEvenWhenCallPostponingIsOff()
    {
        var scheduler = MakeScheduler(Monday(10), new SchedulerConfig { PostponeDuringCalls = false });
        scheduler.SnoozeUntilAfterCall();
        busy.Reasons = [BusyReason.Call];
        Assert.Empty(Run(scheduler, 30));
        busy.Reasons = [];
        Assert.Equal([1], Run(scheduler, 1));
        // Back to normal: calls no longer hold reminders.
        busy.Reasons = [BusyReason.Call];
        Assert.Equal([45], Run(scheduler, 45));
    }

    [Fact]
    public void TestRegularSnoozeCancelsAfterCall()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.SnoozeUntilAfterCall();
        scheduler.Snooze(10);
        Assert.False(scheduler.IsWaitingForCall);
        Assert.Equal([10], Run(scheduler, 10));
    }

    [Fact]
    public void TestQuietHourHoldsRemindersAndRestartsAfter()
    {
        var scheduler = MakeScheduler(Monday(9, 30), new SchedulerConfig { QuietHours = new HashSet<int> { 10 } });
        Assert.False(scheduler.IsWithinActiveHours(Monday(10, 15)));
        Assert.True(scheduler.IsWithinActiveHours(Monday(11)));
        // Due at 10:15, but 10:00–11:00 is quiet. A fresh 45 min starts at 11:00.
        Assert.Empty(Run(scheduler, 30));
        Assert.IsType<SchedulerStatus.OutsideActiveHours>(scheduler.Status);
        Assert.Equal([105], Run(scheduler, 105));
    }

    // Jitter

    [Fact]
    public void TestZeroJitterNeverConsultsTheRandomSource()
    {
        var random = new CountingRandom(() => Assert.Fail("the random source was read with zero jitter"));
        var scheduler = MakeScheduler(Monday(10), random: random);
        Assert.Equal([45, 90], Run(scheduler, 100));
        scheduler.Snooze(5);
        scheduler.Resume();
        scheduler.SessionCompleted();
        scheduler.PauseFor(60);
        _ = Run(scheduler, 2);
        Assert.Equal(0, random.Reads);
    }

    [Fact]
    public void TestJitterOffsetsEveryIntervalFromTheSeed()
    {
        var config = new SchedulerConfig { IntervalSeconds = 30 * 60, IntervalJitterSeconds = 10 * 60 };
        var scheduler = MakeScheduler(Monday(10), config, new SplitMix64(42));
        // SplitMix64(42) first draws 0.74156…: floor(0.4831… × 600 + 0.5) = 290 s past the 30 minutes.
        Assert.Equal(Monday(10).AddSeconds(30 * 60 + 290), scheduler.NextFireDate);
        // Then −408 s (10:35 + 23:12) and −266 s (10:59 + 25:34), each from the firing tick.
        Assert.Equal([35, 59, 85], Run(scheduler, 100));
    }

    [Fact]
    public void TestJitterBoundsAndRounding()
    {
        var config = new SchedulerConfig { IntervalSeconds = 30 * 60, IntervalJitterSeconds = 10 * 60 };
        Assert.Equal(Monday(10, 20), MakeScheduler(Monday(10), config, new ScriptedRandom([0])).NextFireDate);
        Assert.Equal(Monday(10, 30), MakeScheduler(Monday(10), config, new ScriptedRandom([0.5])).NextFireDate);
        Assert.Equal(Monday(10, 40), MakeScheduler(Monday(10), config, new ScriptedRandom([0.999999])).NextFireDate);
    }

    [Fact]
    public void TestJitterIsClampedSoTheIntervalNeverDropsBelowASecond()
    {
        var config = new SchedulerConfig { IntervalSeconds = 60, IntervalJitterSeconds = 10 * 60 };
        // The ±10 minutes clamp to ±59 s: floor(−59 + 0.5) = −59 at the bottom, floor(58.99… + 0.5) = 59 at the top.
        Assert.Equal(Monday(10).AddSeconds(1), MakeScheduler(Monday(10), config, new ScriptedRandom([0])).NextFireDate);
        Assert.Equal(Monday(10).AddSeconds(119), MakeScheduler(Monday(10), config, new ScriptedRandom([0.999999])).NextFireDate);
    }

    [Fact]
    public void TestStatusFallbackUsesThePlainIntervalWithoutReadingTheSource()
    {
        var config = new SchedulerConfig { IntervalSeconds = 30 * 60, IntervalJitterSeconds = 10 * 60 };
        var random = new CountingRandom();
        var scheduler = MakeScheduler(Monday(8), config, random); // outside active hours: no interval drawn yet
        Assert.Null(scheduler.NextFireDate);
        Assert.Equal(0, random.Reads);
        clock.Now = Monday(9); // inside, but no tick has run: the status falls back to now + interval
        Assert.Equal(new SchedulerStatus.Scheduled(Monday(9, 30)), scheduler.Status);
        Assert.Equal(0, random.Reads);
        Assert.Equal(SchedulerDecision.None, scheduler.Tick());
        Assert.Equal(1, random.Reads);
    }

    [Fact]
    public void TestRestoreReplacesTheFreshInterval()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.Restore(Monday(10, 20));
        Assert.Equal(Monday(10, 20), scheduler.NextFireDate);
        Assert.Equal([20, 65], Run(scheduler, 70));
    }

    [Fact]
    public void TestRestoreIsIgnoredForAPastDateWhilePausedAndOutsideActiveHours()
    {
        var scheduler = MakeScheduler(Monday(10));
        scheduler.Restore(Monday(9, 50));
        Assert.Equal(Monday(10, 45), scheduler.NextFireDate);
        scheduler.PauseFor(3600);
        scheduler.Restore(Monday(10, 30));
        Assert.Null(scheduler.NextFireDate);
        var outside = MakeScheduler(Monday(19));
        outside.Restore(Monday(19, 30));
        Assert.Null(outside.NextFireDate);
    }

    [Fact]
    public void TestSnoozingADeliveredReminderRemindsInItsMinutes()
    {
        var scheduler = MakeScheduler(Monday(10));
        _ = Run(scheduler, 20);
        scheduler.Snooze(10);
        Assert.Equal(Monday(10, 45), scheduler.NextFireDate);
        scheduler.ReminderDelivered();
        scheduler.Snooze(10);
        Assert.Equal(Monday(10, 30), scheduler.NextFireDate);
    }

    private ReminderScheduler MakeScheduler(DateTimeOffset start, SchedulerConfig? config = null, IRandomSource? random = null)
    {
        clock = new MockClock(start);
        idle = new MockIdle();
        busy = new MockBusy();
        return new ReminderScheduler(config ?? new SchedulerConfig(), clock, idle, busy, TimeZoneInfo.Utc, random);
    }

    /// <summary>Advances in 20 s ticks (like the app), returning the ticks' decisions.</summary>
    private SchedulerDecision[] Tick(ReminderScheduler scheduler, int seconds)
    {
        var decisions = new List<SchedulerDecision>();
        for (var i = 0; i < seconds / 20; i++)
        {
            clock.Now = clock.Now.AddSeconds(20);
            decisions.Add(scheduler.Tick());
        }
        return decisions.ToArray();
    }

    /// <summary>Advances minute by minute, returning the minutes (from now) at which reminders fired.</summary>
    private int[] Run(ReminderScheduler scheduler, int minutes)
    {
        var fired = new List<int>();
        for (var minute = 1; minute <= minutes; minute++)
        {
            clock.AdvanceMinutes(1);
            if (scheduler.Tick() == SchedulerDecision.Remind)
                fired.Add(minute);
        }
        return fired.ToArray();
    }

    /// <summary>Monday 2026-09-28 at the given time (UTC).</summary>
    private static DateTimeOffset Monday(int hour, int minute = 0) => new(2026, 9, 28, hour, minute, 0, TimeSpan.Zero);
}
