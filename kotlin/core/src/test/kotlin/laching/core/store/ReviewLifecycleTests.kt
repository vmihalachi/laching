package laching.core.store

import laching.core.settings.InMemorySettingsStore
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ReviewLifecycleTests {
    private val zone: ZoneId = ZoneOffset.ofHours(-8)
    private val start: Instant = Instant.ofEpochSecond(1_770_000_000)

    private fun day(offset: Int): Instant = start.plus(Duration.ofDays(offset.toLong()))

    /** Five uses on three days. */
    private val uses = listOf(0, 0, 1, 1, 2).map(::day)

    @Test
    fun `due on the third distinct local day with bounded storage`() {
        val store = InMemorySettingsStore()
        val review = ReviewLifecycle(store)
        repeat(5) { review.countDay(start, zone) }
        assertFalse(review.isDue)
        review.countDay(day(1), zone)
        assertFalse(review.isDue)
        review.countDay(day(2), zone)
        assertTrue(review.isDue)
        for (offset in 3 until 10) review.countDay(day(offset), zone)
        assertEquals(3, store.getStrings("feedback.review.homeDays.v1")?.size)
        assertTrue(ReviewLifecycle(store).isDue)
    }

    @Test
    fun `days follow the time zone`() {
        // 23:30 and 00:30 UTC are one evening at UTC-8, two days at UTC.
        val dates = listOf("2026-10-01T23:30:00Z", "2026-10-02T00:30:00Z").map(Instant::parse)
        val local = ReviewLifecycle(InMemorySettingsStore(), days = 2)
        dates.forEach { local.countDay(it, zone) }
        assertFalse(local.isDue)
        val utc = ReviewLifecycle(InMemorySettingsStore(), days = 2)
        dates.forEach { utc.countDay(it, ZoneOffset.UTC) }
        assertTrue(utc.isDue)
    }

    @Test
    fun `dismissal survives relaunch and forgets the days`() {
        val store = InMemorySettingsStore()
        val review = ReviewLifecycle(store)
        for (offset in 0 until 3) review.countDay(day(offset), zone)
        assertTrue(review.isDue)
        review.stop()
        assertFalse(review.isDue)
        val relaunched = ReviewLifecycle(store)
        for (offset in 0 until 10) relaunched.countDay(day(offset), zone)
        assertFalse(relaunched.isDue)
        assertTrue(relaunched.isStopped)
        assertNull(store.getStrings("feedback.review.homeDays.v1"))
    }

    @Test
    fun `stopping before eligibility never asks`() {
        val store = InMemorySettingsStore()
        val review = ReviewLifecycle(store)
        review.stop()
        for (offset in 0 until 3) review.countDay(day(offset), zone)
        review.countUses(uses, minimum = 5, zone = zone)
        assertFalse(review.isDue)
        assertFalse(ReviewLifecycle(store).isDue)
    }

    @Test
    fun `eligibility needs enough uses on enough local days`() {
        assertFalse(ReviewLifecycle.isEligible(uses.take(4), 5, 3, zone))
        assertFalse(ReviewLifecycle.isEligible(List(5) { start }, 5, 3, zone))
        assertFalse(ReviewLifecycle.isEligible(listOf(0, 0, 2, 2, 2).map(::day), 5, 3, zone))
        assertTrue(ReviewLifecycle.isEligible(uses, 5, 3, zone))
        val dates =
            listOf(
                "2026-10-01T23:30:00Z",
                "2026-10-02T00:30:00Z",
                "2026-10-02T12:00:00Z",
                "2026-10-02T14:00:00Z",
                "2026-10-03T00:30:00Z",
            ).map(Instant::parse)
        assertFalse(ReviewLifecycle.isEligible(dates, 5, 3, zone))
        assertTrue(ReviewLifecycle.isEligible(dates, 5, 3, ZoneOffset.UTC))
    }

    @Test
    fun `one attempt that survives relaunch`() {
        val store = InMemorySettingsStore()
        val review = ReviewLifecycle(store)
        assertFalse(review.beginAttempt())
        review.countUses(uses.take(4), minimum = 5, zone = zone)
        assertFalse(review.isDue)
        review.countUses(uses, minimum = 5, zone = zone)
        assertTrue(review.isDue)
        // Due stays due when the history later shrinks.
        review.countUses(emptyList(), minimum = 5, zone = zone)
        assertTrue(ReviewLifecycle(store).isDue)
        assertTrue(review.beginAttempt())
        assertFalse(review.isDue)
        val relaunched = ReviewLifecycle(store)
        relaunched.countUses(uses, minimum = 5, zone = zone)
        assertTrue(relaunched.isStopped)
        assertFalse(relaunched.beginAttempt())
    }

    @Test
    fun `the app's link cancels a pending request`() {
        val store = InMemorySettingsStore()
        val review = ReviewLifecycle(store)
        review.countUses(uses, minimum = 5, zone = zone)
        review.stop()
        review.countUses(uses, minimum = 5, zone = zone)
        assertFalse(review.isDue)
        assertFalse(review.beginAttempt())
        assertTrue(ReviewLifecycle(store).isStopped)
        assertNull(store.getBool("feedback.review.pending.v1"))
    }

    @Test
    fun `each instance reads the shared store`() {
        val store = InMemorySettingsStore()
        ReviewLifecycle(store).countUses(uses, minimum = 5, zone = zone)
        assertTrue(ReviewLifecycle(store).beginAttempt())
        assertFalse(ReviewLifecycle(store).beginAttempt())
    }
}
