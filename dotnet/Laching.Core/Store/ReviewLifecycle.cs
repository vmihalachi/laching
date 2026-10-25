using System.Globalization;
using Laching.Core.Settings;

namespace Laching.Core.Store;

/// <summary>
/// When an app may ask for a review, and the promise that it then stops. An ask becomes due after enough use, counted
/// one of two ways: <see cref="CountDay"/> for each day the app is opened (due on the <see cref="Days"/>th distinct
/// local day), or <see cref="CountUses"/> over a history the app keeps (due once it holds <c>minimum</c> uses on
/// <see cref="Days"/> distinct local days). It stops for good on <see cref="Stop"/> (the person dismissed the ask, or
/// left a review from the app's own link) or on <see cref="BeginAttempt"/> (the one native request, stopped before it
/// is made, so a request the Store suppresses or that fails is never retried). Never infers whether someone rated,
/// and never asks by itself: the app shows its invitation while <see cref="IsDue"/>, or makes the native request when
/// <see cref="BeginAttempt"/> says so. Mirrors Swift's <c>ReviewLifecycle</c> (LachingStore).
/// <para>
/// Its keys (<c>feedback.review.*</c>) sit beside the app's preferences in the store but are never among them, so
/// resetting the preferences (Start over) keeps them.
/// </para>
/// </summary>
public sealed class ReviewLifecycle(ISettingsStore store, int days = 3)
{
    private const string StoppedKey = "feedback.review.stopped.v1";
    private const string DaysKey = "feedback.review.homeDays.v1";
    private const string PendingKey = "feedback.review.pending.v1";

    /// <summary>Distinct local days of use before an ask is due.</summary>
    public int Days { get; } = days;

    /// <summary>Stopped for good: dismissed, reviewed from the app's link, or the native request attempted.</summary>
    public bool IsStopped => store.GetBool(StoppedKey) == true;

    /// <summary>An ask may be shown, or the native request attempted. False once stopped.</summary>
    public bool IsDue => !IsStopped && (store.GetBool(PendingKey) == true || (store.GetStrings(DaysKey)?.Count ?? 0) >= Days);

    /// <summary>Counts the local day <paramref name="now"/> falls on in <paramref name="zone"/> as a day of use. Keeps at most <see cref="Days"/> days.</summary>
    public void CountDay(DateTimeOffset now, TimeZoneInfo zone)
    {
        if (IsStopped) return;
        var counted = (store.GetStrings(DaysKey) ?? []).Distinct().Order(StringComparer.Ordinal).ToList();
        var day = TimeZoneInfo.ConvertTime(now, zone).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (counted.Count < Days && !counted.Contains(day))
        {
            counted.Add(day);
            store.SetStrings(DaysKey, counted);
        }
    }

    /// <summary>
    /// For an app that keeps a history of uses: due from now on once <paramref name="dates"/> (every use, any order)
    /// pass <see cref="IsEligible"/>. Call it after each new use; a later, shorter history never takes the ask back.
    /// </summary>
    public void CountUses(IEnumerable<DateTimeOffset> dates, int minimum, TimeZoneInfo zone)
    {
        if (!IsStopped && IsEligible(dates, minimum, Days, zone)) store.SetBool(PendingKey, true);
    }

    /// <summary>At least <paramref name="minimum"/> uses, on at least <paramref name="days"/> distinct local days in <paramref name="zone"/>.</summary>
    public static bool IsEligible(IEnumerable<DateTimeOffset> dates, int minimum, int days, TimeZoneInfo zone)
    {
        var all = dates.ToArray();
        return all.Length >= minimum
            && all.Select(d => DateOnly.FromDateTime(TimeZoneInfo.ConvertTime(d, zone).DateTime)).Distinct().Count() >= days;
    }

    /// <summary>Never asks again. Called when the person dismisses the ask or leaves a review from the app's own link.</summary>
    public void Stop()
    {
        store.SetBool(StoppedKey, true);
        store.Remove(DaysKey);
        store.Remove(PendingKey);
    }

    /// <summary>The one native request: true when due, and then already stopped, so the app makes the request exactly once.</summary>
    public bool BeginAttempt()
    {
        if (!IsDue) return false;
        Stop();
        return true;
    }
}
