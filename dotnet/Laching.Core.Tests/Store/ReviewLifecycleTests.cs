using Laching.Core.Settings;
using Laching.Core.Store;

namespace Laching.Core.Tests.Store;

public sealed class ReviewLifecycleTests
{
    private static readonly TimeZoneInfo Zone = TimeZoneInfo.CreateCustomTimeZone("test", TimeSpan.FromHours(-8), "test", "test");
    private static readonly DateTimeOffset Start = DateTimeOffset.FromUnixTimeSeconds(1_770_000_000);
    private static DateTimeOffset Day(int offset) => Start.AddDays(offset);

    /// <summary>Five uses on three days.</summary>
    private static readonly DateTimeOffset[] Uses = [Day(0), Day(0), Day(1), Day(1), Day(2)];

    [Fact]
    public void DueOnTheThirdDistinctLocalDayWithBoundedStorage()
    {
        var store = new InMemorySettingsStore();
        var review = new ReviewLifecycle(store);
        for (var i = 0; i < 5; i++) review.CountDay(Start, Zone);
        Assert.False(review.IsDue);
        review.CountDay(Day(1), Zone);
        Assert.False(review.IsDue);
        review.CountDay(Day(2), Zone);
        Assert.True(review.IsDue);
        for (var offset = 3; offset < 10; offset++) review.CountDay(Day(offset), Zone);
        Assert.Equal(3, store.GetStrings("feedback.review.homeDays.v1")?.Count);
        Assert.True(new ReviewLifecycle(store).IsDue);
    }

    [Fact]
    public void DaysFollowTheTimeZone()
    {
        // 23:30 and 00:30 UTC are one evening at UTC-8, two days at UTC.
        DateTimeOffset[] dates = [DateTimeOffset.Parse("2026-10-01T23:30:00Z"), DateTimeOffset.Parse("2026-10-02T00:30:00Z")];
        var local = new ReviewLifecycle(new InMemorySettingsStore(), days: 2);
        foreach (var date in dates) local.CountDay(date, Zone);
        Assert.False(local.IsDue);
        var utc = new ReviewLifecycle(new InMemorySettingsStore(), days: 2);
        foreach (var date in dates) utc.CountDay(date, TimeZoneInfo.Utc);
        Assert.True(utc.IsDue);
    }

    [Fact]
    public void DismissalSurvivesRelaunchAndForgetsTheDays()
    {
        var store = new InMemorySettingsStore();
        var review = new ReviewLifecycle(store);
        for (var offset = 0; offset < 3; offset++) review.CountDay(Day(offset), Zone);
        Assert.True(review.IsDue);
        review.Stop();
        Assert.False(review.IsDue);
        var relaunched = new ReviewLifecycle(store);
        for (var offset = 0; offset < 10; offset++) relaunched.CountDay(Day(offset), Zone);
        Assert.False(relaunched.IsDue);
        Assert.True(relaunched.IsStopped);
        Assert.Null(store.GetStrings("feedback.review.homeDays.v1"));
    }

    [Fact]
    public void StoppingBeforeEligibilityNeverAsks()
    {
        var store = new InMemorySettingsStore();
        var review = new ReviewLifecycle(store);
        review.Stop();
        for (var offset = 0; offset < 3; offset++) review.CountDay(Day(offset), Zone);
        review.CountUses(Uses, minimum: 5, Zone);
        Assert.False(review.IsDue);
        Assert.False(new ReviewLifecycle(store).IsDue);
    }

    [Fact]
    public void EligibilityNeedsEnoughUsesOnEnoughLocalDays()
    {
        Assert.False(ReviewLifecycle.IsEligible(Uses.Take(4), 5, 3, Zone));
        Assert.False(ReviewLifecycle.IsEligible(Enumerable.Repeat(Start, 5), 5, 3, Zone));
        Assert.False(ReviewLifecycle.IsEligible([Day(0), Day(0), Day(2), Day(2), Day(2)], 5, 3, Zone));
        Assert.True(ReviewLifecycle.IsEligible(Uses, 5, 3, Zone));
        var dates = new[] { "2026-10-01T23:30:00Z", "2026-10-02T00:30:00Z", "2026-10-02T12:00:00Z", "2026-10-02T14:00:00Z", "2026-10-03T00:30:00Z" }
            .Select(s => DateTimeOffset.Parse(s)).ToArray();
        Assert.False(ReviewLifecycle.IsEligible(dates, 5, 3, Zone));
        Assert.True(ReviewLifecycle.IsEligible(dates, 5, 3, TimeZoneInfo.Utc));
    }

    [Fact]
    public void OneAttemptThatSurvivesRelaunch()
    {
        var store = new InMemorySettingsStore();
        var review = new ReviewLifecycle(store);
        Assert.False(review.BeginAttempt());
        review.CountUses(Uses.Take(4), minimum: 5, Zone);
        Assert.False(review.IsDue);
        review.CountUses(Uses, minimum: 5, Zone);
        Assert.True(review.IsDue);
        // Due stays due when the history later shrinks.
        review.CountUses([], minimum: 5, Zone);
        Assert.True(new ReviewLifecycle(store).IsDue);
        Assert.True(review.BeginAttempt());
        Assert.False(review.IsDue);
        var relaunched = new ReviewLifecycle(store);
        relaunched.CountUses(Uses, minimum: 5, Zone);
        Assert.True(relaunched.IsStopped);
        Assert.False(relaunched.BeginAttempt());
    }

    [Fact]
    public void TheAppsLinkCancelsAPendingRequest()
    {
        var store = new InMemorySettingsStore();
        var review = new ReviewLifecycle(store);
        review.CountUses(Uses, minimum: 5, Zone);
        review.Stop();
        review.CountUses(Uses, minimum: 5, Zone);
        Assert.False(review.IsDue);
        Assert.False(review.BeginAttempt());
        Assert.True(new ReviewLifecycle(store).IsStopped);
        Assert.Null(store.GetBool("feedback.review.pending.v1"));
    }

    [Fact]
    public void EachInstanceReadsTheSharedStore()
    {
        var store = new InMemorySettingsStore();
        new ReviewLifecycle(store).CountUses(Uses, minimum: 5, Zone);
        Assert.True(new ReviewLifecycle(store).BeginAttempt());
        Assert.False(new ReviewLifecycle(store).BeginAttempt());
    }
}
