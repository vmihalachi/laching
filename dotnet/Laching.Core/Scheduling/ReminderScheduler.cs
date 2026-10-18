using Laching.Core.Random;

namespace Laching.Core.Scheduling;

/// <summary>
/// Pure reminder scheduling. Call <see cref="Tick"/> periodically (every 20–30 s).
/// Fires a recurring reminder inside the active hours and weekdays, skips quiet hours, treats being idle as a
/// break that restarts the interval, holds a due reminder while the user is busy (a call, a full-screen app)
/// and releases it after a grace period, and supports snooze, "after my next call" and pause. Time comes from
/// the injected <see cref="IClock"/>, user state from the injected providers and any randomness (interval jitter)
/// from the injected <see cref="IRandomSource"/>, so every decision is reproducible: conformance/scheduler pins it.
/// </summary>
public sealed class ReminderScheduler
{
    private readonly IClock clock;
    private readonly IIdleTimeProvider idle;
    private readonly IBusyStateProvider busy;
    private readonly IRandomSource random;
    private DateTimeOffset? lastBusyAt;
    private BusyReason lastBusyReason = BusyReason.Call;

    public ReminderScheduler(
        SchedulerConfig config,
        IClock clock,
        IIdleTimeProvider idle,
        IBusyStateProvider? busy = null,
        TimeZoneInfo? timeZone = null,
        IRandomSource? random = null
    )
    {
        Config = config;
        this.clock = clock;
        this.idle = idle;
        this.busy = busy ?? new NeverBusy();
        this.random = random ?? new SystemRandom();
        TimeZone = timeZone ?? TimeZoneInfo.Local;
        if (IsWithinActiveHours(clock.Now))
            NextFireDate = clock.Now.AddSeconds(NextInterval());
    }

    public SchedulerConfig Config { get; private set; }

    /// <summary>The zone whose days, hours and weekdays the active window, quiet hours and <see cref="EndOfToday"/> use.</summary>
    public TimeZoneInfo TimeZone { get; set; }

    public DateTimeOffset? NextFireDate { get; private set; }

    public DateTimeOffset? PausedUntil { get; private set; }

    public bool IsAway { get; private set; }

    /// <summary>A reminder came due while busy and fires once the user is free.</summary>
    public bool IsHolding { get; private set; }

    /// <summary>"After this call" snooze is on: calls hold the reminder even if that setting is off.</summary>
    public bool IsAfterCallArmed { get; private set; }

    /// <summary>"After this call" snooze is waiting for a call to start.</summary>
    public bool IsWaitingForCall => IsAfterCallArmed && !IsHolding;

    /// <summary>The camera or mic is in use right now, so a menu can offer "After this call".</summary>
    public bool IsInCall => busy.CurrentBusyReasons().Contains(BusyReason.Call);

    /// <summary>A reminder fired and nothing has answered it yet (start, skip, snooze, pause, resume, away).</summary>
    public bool HasUnansweredReminder { get; private set; }

    public SchedulerStatus Status
    {
        get
        {
            var now = clock.Now;
            if (PausedUntil is { } pausedUntil && now < pausedUntil)
                return new SchedulerStatus.Paused(pausedUntil);
            if (!IsWithinActiveHours(now))
                return new SchedulerStatus.OutsideActiveHours();
            if (IsHolding)
                return new SchedulerStatus.Postponed(lastBusyReason);
            if (IsAway)
                return new SchedulerStatus.Away();
            // A read-only fallback, so it uses the plain interval and never consumes a random number.
            return new SchedulerStatus.Scheduled(NextFireDate ?? now.AddSeconds(Config.IntervalSeconds));
        }
    }

    /// <summary>Start of the next calendar day in <see cref="TimeZone"/>, for "Pause for the rest of today".</summary>
    public DateTimeOffset EndOfToday => AddLocalDays(StartOfDay(clock.Now), 1);

    public void Update(SchedulerConfig config)
    {
        var intervalChanged = config.IntervalSeconds != Config.IntervalSeconds;
        Config = config;
        var now = clock.Now;
        if (intervalChanged || NextFireDate is null)
        {
            ClearBusyState();
            NextFireDate = IsWithinActiveHours(now) ? now.AddSeconds(NextInterval()) : null;
        }
    }

    public bool IsWithinActiveHours(DateTimeOffset date)
    {
        var local = LocalDateTime(date);
        if (!Config.ActiveWeekdays.Contains((int)local.DayOfWeek + 1))
            return false;
        if (Config.QuietHours.Contains(local.Hour))
            return false;
        var minute = local.Hour * 60 + local.Minute;
        if (Config.ActiveStartMinute <= Config.ActiveEndMinute)
            return minute >= Config.ActiveStartMinute && minute < Config.ActiveEndMinute;
        // Overnight window, e.g. 22:00 - 06:00.
        return minute >= Config.ActiveStartMinute || minute < Config.ActiveEndMinute;
    }

    public SchedulerDecision Tick()
    {
        var now = clock.Now;

        if (PausedUntil is { } pausedUntil)
        {
            if (now < pausedUntil)
                return SchedulerDecision.None;
            PausedUntil = null;
            NextFireDate = now.AddSeconds(NextInterval());
        }

        if (!IsWithinActiveHours(now))
        {
            NextFireDate = null;
            IsAway = false;
            ClearBusyState();
            return SchedulerDecision.None;
        }

        // Entering the active window: start a fresh interval.
        if (NextFireDate is not { } fire)
        {
            NextFireDate = now.AddSeconds(NextInterval());
            return SchedulerDecision.None;
        }

        // Busy: a call or full-screen app isn't a break, so idle doesn't reset the timer.
        var watched = Config.PostponeReasons.ToHashSet();
        if (IsAfterCallArmed)
            watched.Add(BusyReason.Call);
        var reasons = busy.CurrentBusyReasons().Where(watched.Contains).ToHashSet();
        if (reasons.Count > 0)
        {
            lastBusyAt = now;
            lastBusyReason = reasons.Contains(BusyReason.Call) ? BusyReason.Call : BusyReason.FullScreen;
            IsAway = false;
            if ((IsWaitingForCall && reasons.Contains(BusyReason.Call)) || now >= fire)
                IsHolding = true;
            return IsHolding ? SchedulerDecision.Postpone : SchedulerDecision.None;
        }

        // Away from the desk counts as a break: keep pushing the reminder out.
        if (idle.SecondsSinceLastInput() >= Config.IdleThresholdSeconds)
        {
            IsAway = true;
            ClearBusyState();
            NextFireDate = now.AddSeconds(NextInterval());
            return SchedulerDecision.None;
        }
        IsAway = false;

        if (IsHolding)
        {
            if (lastBusyAt is { } last && (now - last).TotalSeconds < Config.BusyGraceSeconds)
                return SchedulerDecision.Postpone;
            return FireReminder(now);
        }
        return now >= fire ? FireReminder(now) : SchedulerDecision.None;
    }

    /// <summary>
    /// Answering a reminder (unanswered, held during a call, or already snoozed until after a call) reminds again
    /// in <paramref name="minutes"/>. Otherwise snooze only ever pushes the next reminder later, never closer.
    /// </summary>
    public void Snooze(int minutes = 10) => SnoozeFor(minutes * 60.0);

    /// <summary><see cref="Snooze"/> for a duration in seconds, for a snooze that isn't whole minutes (say 90 seconds).</summary>
    public void SnoozeFor(double seconds)
    {
        var snoozed = clock.Now.AddSeconds(seconds);
        var answering = HasUnansweredReminder || IsHolding || IsAfterCallArmed;
        PausedUntil = null;
        ClearBusyState();
        NextFireDate = answering || !(NextFireDate > snoozed) ? snoozed : NextFireDate;
    }

    /// <summary>
    /// Remind shortly after the current (or next) call ends. If no call starts within
    /// <see cref="SchedulerConfig.AfterCallFallbackSeconds"/>, remind then, like a longer snooze.
    /// </summary>
    public void SnoozeUntilAfterCall()
    {
        PausedUntil = null;
        ClearBusyState();
        IsAfterCallArmed = true;
        NextFireDate = clock.Now.AddSeconds(Config.AfterCallFallbackSeconds);
    }

    public void PauseFor(double seconds) => PauseUntil(clock.Now.AddSeconds(seconds));

    public void PauseUntil(DateTimeOffset date)
    {
        PausedUntil = date;
        ClearBusyState();
        NextFireDate = null;
    }

    public void Resume()
    {
        PausedUntil = null;
        ClearBusyState();
        NextFireDate = clock.Now.AddSeconds(NextInterval());
    }

    /// <summary>A session finished (or a reminder was skipped): restart the full interval.</summary>
    public void SessionCompleted()
    {
        ClearBusyState();
        NextFireDate = clock.Now.AddSeconds(NextInterval());
    }

    /// <summary>
    /// Picks up the next reminder an earlier run of the app scheduled, for an app the system ends between reminders:
    /// <paramref name="date"/> replaces the interval drawn at creation, so a relaunch doesn't push the reminder out.
    /// Ignored while paused, outside the active hours and for a date not after now.
    /// </summary>
    public void Restore(DateTimeOffset date)
    {
        var now = clock.Now;
        if (PausedUntil is not null || !IsWithinActiveHours(now) || date <= now)
            return;
        NextFireDate = date;
    }

    /// <summary>
    /// A reminder went out without <see cref="Tick"/>: the system delivered one the app scheduled ahead. Until it's
    /// answered, <see cref="Snooze"/> reminds again in its minutes, as after a reminder <see cref="Tick"/> fired.
    /// </summary>
    public void ReminderDelivered() => HasUnansweredReminder = true;

    private SchedulerDecision FireReminder(DateTimeOffset now)
    {
        ClearBusyState();
        HasUnansweredReminder = true;
        NextFireDate = now.AddSeconds(NextInterval());
        return SchedulerDecision.Remind;
    }

    private void ClearBusyState()
    {
        IsHolding = false;
        IsAfterCallArmed = false;
        lastBusyAt = null;
        HasUnansweredReminder = false;
    }

    /// <summary>
    /// The next interval: <see cref="SchedulerConfig.IntervalSeconds"/> plus a whole-second random offset within
    /// ±<see cref="SchedulerConfig.IntervalJitterSeconds"/>, the jitter clamped to the interval less a second so the
    /// next interval is never shorter than a second. With zero jitter the random source is never consulted, so the
    /// committed vectors stay exact.
    /// </summary>
    private double NextInterval()
    {
        if (Config.IntervalJitterSeconds > 0)
        {
            var jitter = Math.Min(Config.IntervalJitterSeconds, Math.Max(Config.IntervalSeconds - 1, 0));
            var unit = random.NextUnit();
            return Config.IntervalSeconds + Math.Floor((unit * 2 - 1) * jitter + 0.5);
        }
        return Config.IntervalSeconds;
    }

    // Local-time arithmetic in TimeZone, the way Foundation's Calendar does it in the Swift reference.

    private DateTime LocalDateTime(DateTimeOffset instant) => TimeZoneInfo.ConvertTime(instant, TimeZone).DateTime;

    /// <summary>
    /// The instant at a local wall-clock time; a time skipped by DST moves forward, an ambiguous one takes the
    /// earlier offset.
    /// </summary>
    private DateTimeOffset AtLocal(DateTime local)
    {
        while (TimeZone.IsInvalidTime(local))
            local = local.AddMinutes(1);
        var offset = TimeZone.IsAmbiguousTime(local)
            ? TimeZone.GetAmbiguousTimeOffsets(local).Max()
            : TimeZone.GetUtcOffset(local);
        return new DateTimeOffset(DateTime.SpecifyKind(local, DateTimeKind.Unspecified), offset);
    }

    private DateTimeOffset StartOfDay(DateTimeOffset instant) => AtLocal(LocalDateTime(instant).Date);

    private DateTimeOffset AddLocalDays(DateTimeOffset instant, int days) =>
        AtLocal(LocalDateTime(instant).AddDays(days));
}
