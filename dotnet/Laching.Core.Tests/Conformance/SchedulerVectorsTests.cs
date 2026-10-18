using System.Text.Json;
using Laching.Core.Random;
using Laching.Core.Scheduling;
using static Laching.Testing.Conformance.VectorSupport;

namespace Laching.Core.Tests;

/// <summary>
/// Replays conformance/scheduler/*.json the way conformance/README.md describes: build the config and a seeded
/// SplitMix64, run every step, and compare the decisions and the full state after each one with the Swift reference.
/// </summary>
public sealed class SchedulerVectorsTests
{
    public static IEnumerable<object[]> Vectors => ConformanceRoot.Files("scheduler");

    [Theory]
    [MemberData(nameof(Vectors))]
    public void ReplaysVector(string path)
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var root = doc.RootElement;
        var tz = ConformanceSupport.TimeZone(root.GetProperty("timeZone").GetString()!);
        var clock = new MockClock(ConformanceSupport.Date(root.GetProperty("start").GetString()!));
        var idle = new MockIdle();
        var busy = new MockBusy();
        var random = new SplitMix64(OptionalUInt64(root, "seed", 0));
        var scheduler = new ReminderScheduler(ReadConfig(root.GetProperty("config")), clock, idle, busy, tz, random);
        var replay = new Replay(scheduler, clock, idle, busy, tz);
        Assert.Equal(ReadState(root.GetProperty("initialState"), "initialState"), replay.Snapshot("initialState"));
        var index = 0;
        foreach (var step in root.GetProperty("steps").EnumerateArray())
        {
            replay.Run(step, $"step {index} ({step.GetProperty("op").GetString()})");
            index++;
        }
    }

    private static SchedulerConfig ReadConfig(JsonElement c) =>
        new()
        {
            IntervalSeconds = c.GetProperty("intervalSeconds").GetDouble(),
            ActiveStartMinute = c.GetProperty("activeStartMinute").GetInt32(),
            ActiveEndMinute = c.GetProperty("activeEndMinute").GetInt32(),
            ActiveWeekdays = c.GetProperty("activeWeekdays").EnumerateArray().Select(x => x.GetInt32()).ToHashSet(),
            QuietHours = c.GetProperty("quietHours").EnumerateArray().Select(x => x.GetInt32()).ToHashSet(),
            IdleThresholdSeconds = c.GetProperty("idleThresholdSeconds").GetDouble(),
            PostponeDuringCalls = c.GetProperty("postponeDuringCalls").GetBoolean(),
            PostponeDuringFullScreen = c.GetProperty("postponeDuringFullScreen").GetBoolean(),
            BusyGraceSeconds = c.GetProperty("busyGraceSeconds").GetDouble(),
            AfterCallFallbackSeconds = c.GetProperty("afterCallFallbackSeconds").GetDouble(),
            IntervalJitterSeconds = OptionalDouble(c, "intervalJitterSeconds", 0),
        };

    private static State ReadState(JsonElement e, string where)
    {
        var status = e.GetProperty("status");
        return new State(
            where,
            e.GetProperty("now").GetString()!,
            new Status(status.GetProperty("type").GetString()!, OptionalString(status, "date"), OptionalString(status, "reason")),
            OptionalString(e, "nextFireDate"),
            OptionalString(e, "pausedUntil"),
            e.GetProperty("isAway").GetBoolean(),
            e.GetProperty("isHolding").GetBoolean(),
            e.GetProperty("isWaitingForCall").GetBoolean(),
            e.GetProperty("endOfToday").GetString()!
        );
    }

    private static TickDecision[] ReadDecisions(JsonElement expect) =>
        expect
            .GetProperty("decisions")
            .EnumerateArray()
            .Select(d => new TickDecision(d.GetProperty("tick").GetInt32(), d.GetProperty("decision").GetString()!))
            .ToArray();

    private static BusyReason ParseBusyOrThrow(string name) =>
        ParseBusy(name) ?? throw new InvalidOperationException($"unknown busy reason {name}");

    private static string DecisionName(SchedulerDecision decision) =>
        decision == SchedulerDecision.Remind ? "remind" : "postpone";

    private sealed record Status(string Type, string? Date, string? Reason);

    private sealed record State(
        string Where,
        string Now,
        Status Status,
        string? NextFireDate,
        string? PausedUntil,
        bool IsAway,
        bool IsHolding,
        bool IsWaitingForCall,
        string EndOfToday
    );

    private sealed record TickDecision(int Tick, string Decision);

    private sealed class Replay(ReminderScheduler scheduler, MockClock clock, MockIdle idle, MockBusy busy, TimeZoneInfo tz)
    {
        public void Run(JsonElement step, string where)
        {
            var expect = step.GetProperty("expect");
            switch (step.GetProperty("op").GetString())
            {
                case "ticks":
                    Assert.Equal(ReadDecisions(expect), Ticks(step));
                    break;
                case "setTime":
                    clock.Now = ConformanceSupport.Date(step.GetProperty("time").GetString()!);
                    break;
                case "snooze":
                    scheduler.Snooze(step.GetProperty("minutes").GetInt32());
                    break;
                case "snoozeFor":
                    scheduler.SnoozeFor(step.GetProperty("seconds").GetInt32());
                    break;
                case "snoozeUntilAfterCall":
                    scheduler.SnoozeUntilAfterCall();
                    break;
                case "pauseFor":
                    scheduler.PauseFor(step.GetProperty("seconds").GetInt32());
                    break;
                case "pauseUntil":
                    scheduler.PauseUntil(ConformanceSupport.Date(step.GetProperty("time").GetString()!));
                    break;
                case "pauseUntilEndOfToday":
                    scheduler.PauseUntil(scheduler.EndOfToday);
                    break;
                case "resume":
                    scheduler.Resume();
                    break;
                case "sessionCompleted":
                    scheduler.SessionCompleted();
                    break;
                case "updateConfig":
                    scheduler.Update(ReadConfig(step.GetProperty("config")));
                    break;
                case "reminderDelivered":
                    scheduler.ReminderDelivered();
                    break;
                case "restore":
                    scheduler.Restore(ConformanceSupport.Date(step.GetProperty("time").GetString()!));
                    break;
                case "isWithinActiveHours":
                    var asked = ConformanceSupport.Date(step.GetProperty("time").GetString()!);
                    Assert.Equal(expect.GetProperty("result").GetBoolean(), scheduler.IsWithinActiveHours(asked));
                    break;
                default:
                    throw new InvalidOperationException($"{where}: unknown op");
            }
            Assert.Equal(ReadState(expect.GetProperty("state"), where), Snapshot(where));
        }

        public State Snapshot(string where)
        {
            var status = scheduler.Status switch
            {
                SchedulerStatus.Scheduled s => new Status("scheduled", Iso(s.Date), null),
                SchedulerStatus.Paused p => new Status("paused", Iso(p.Until), null),
                SchedulerStatus.OutsideActiveHours => new Status("outsideActiveHours", null, null),
                SchedulerStatus.Away => new Status("away", null, null),
                SchedulerStatus.Postponed p => new Status("postponed", null, BusyName(p.Reason)),
                var other => throw new InvalidOperationException(other.ToString()),
            };
            return new State(
                where,
                Iso(clock.Now),
                status,
                scheduler.NextFireDate is { } next ? Iso(next) : null,
                scheduler.PausedUntil is { } until ? Iso(until) : null,
                scheduler.IsAway,
                scheduler.IsHolding,
                scheduler.IsWaitingForCall,
                Iso(scheduler.EndOfToday)
            );
        }

        /// <summary>Runs a "ticks" step and returns the 1-based ticks that returned remind or postpone.</summary>
        private TickDecision[] Ticks(JsonElement step)
        {
            idle.Idle = OptionalDouble(step, "idleSeconds", 0);
            busy.Reasons = OptionalStrings(step, "busy").Select(ParseBusyOrThrow).ToHashSet();
            var count = step.GetProperty("count").GetInt32();
            var every = OptionalInt(step, "everySeconds", 0);
            var decisions = new List<TickDecision>();
            for (var tick = 1; tick <= count; tick++)
            {
                clock.Now = clock.Now.AddSeconds(every);
                var decision = scheduler.Tick();
                if (decision != SchedulerDecision.None)
                    decisions.Add(new TickDecision(tick, DecisionName(decision)));
            }
            return decisions.ToArray();
        }

        private string Iso(DateTimeOffset date) => ConformanceSupport.Iso(date, tz);
    }
}
