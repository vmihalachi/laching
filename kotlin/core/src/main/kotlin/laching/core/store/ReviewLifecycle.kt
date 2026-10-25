package laching.core.store

import laching.core.settings.SettingsStore
import java.time.Instant
import java.time.ZoneId

/**
 * When an app may ask for a review, and the promise that it then stops. An ask becomes due after enough use, counted
 * one of two ways: [countDay] for each day the app is opened (due on the [days]th distinct local day), or [countUses]
 * over a history the app keeps (due once it holds `minimum` uses on [days] distinct local days). It stops for good on
 * [stop] (the person dismissed the ask, or left a review from the app's own link) or on [beginAttempt] (the one native
 * request, stopped before it is made, so a request the store suppresses or that fails is never retried). Never infers
 * whether someone rated, and never asks by itself: the app shows its invitation while [isDue], or makes the native
 * request when [beginAttempt] says so. Mirrors Swift's `ReviewLifecycle` (LachingStore).
 *
 * Its keys (`feedback.review.*`) sit beside the app's preferences in [store] but are never among them, so resetting
 * the preferences (Start over) keeps them.
 */
public class ReviewLifecycle(
    private val store: SettingsStore,
    /** Distinct local days of use before an ask is due. */
    public val days: Int = 3,
) {
    /** Stopped for good: dismissed, reviewed from the app's link, or the native request attempted. */
    public val isStopped: Boolean get() = store.getBool(STOPPED) == true

    /** An ask may be shown, or the native request attempted. False once stopped. */
    public val isDue: Boolean
        get() = !isStopped && (store.getBool(PENDING) == true || (store.getStrings(DAYS)?.size ?: 0) >= days)

    /** Counts the local day [now] falls on in [zone] as a day of use. Keeps at most [days] days. */
    public fun countDay(
        now: Instant,
        zone: ZoneId,
    ) {
        if (isStopped) return
        val counted = (store.getStrings(DAYS) ?: emptyList()).distinct().sorted().toMutableList()
        val day = now.atZone(zone).toLocalDate().toString()
        if (counted.size < days && day !in counted) {
            counted.add(day)
            store.setStrings(DAYS, counted)
        }
    }

    /**
     * For an app that keeps a history of uses: due from now on once [dates] (every use, any order) pass [isEligible].
     * Call it after each new use; a later, shorter history never takes the ask back.
     */
    public fun countUses(
        dates: List<Instant>,
        minimum: Int,
        zone: ZoneId,
    ) {
        if (!isStopped && isEligible(dates, minimum, days, zone)) store.setBool(PENDING, true)
    }

    /** Never asks again. Called when the person dismisses the ask or leaves a review from the app's own link. */
    public fun stop() {
        store.setBool(STOPPED, true)
        store.remove(DAYS)
        store.remove(PENDING)
    }

    /** The one native request: true when due, and then already stopped, so the app makes the request exactly once. */
    public fun beginAttempt(): Boolean {
        if (!isDue) return false
        stop()
        return true
    }

    public companion object {
        private const val STOPPED = "feedback.review.stopped.v1"
        private const val DAYS = "feedback.review.homeDays.v1"
        private const val PENDING = "feedback.review.pending.v1"

        /** At least [minimum] uses, on at least [days] distinct local days in [zone]. */
        public fun isEligible(
            dates: List<Instant>,
            minimum: Int,
            days: Int,
            zone: ZoneId,
        ): Boolean = dates.size >= minimum && dates.map { it.atZone(zone).toLocalDate() }.distinct().size >= days
    }
}
