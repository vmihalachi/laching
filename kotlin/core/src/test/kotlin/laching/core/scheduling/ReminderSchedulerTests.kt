package laching.core.scheduling

import laching.core.random.RandomSource
import laching.testing.CountingRandom
import laching.testing.MockBusy
import laching.testing.MockClock
import laching.testing.MockIdle
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.fail

/**
 * What `ReminderSchedulerTests.swift` and `.cs` check beside the vectors: when [ReminderScheduler] reads its random
 * source, and where today ends when daylight saving moves midnight.
 */
class ReminderSchedulerTests {
    private val zone = ZoneId.of("UTC")
    private lateinit var clock: MockClock

    /** Monday 2026-09-28, a working day on the default schedule (9–18, Monday to Friday, 45 min). */
    private fun monday(
        hour: Int,
        minute: Int = 0,
    ): Instant = LocalDateTime.of(2026, 9, 28, hour, minute).atZone(zone).toInstant()

    private fun makeScheduler(
        start: Instant,
        config: SchedulerConfig = SchedulerConfig(),
        random: RandomSource,
    ): ReminderScheduler {
        clock = MockClock(start)
        return ReminderScheduler(config, clock, MockIdle(), MockBusy(), timeZone = zone, random = random)
    }

    /** Advances minute by minute, returning the minutes that reminded. */
    private fun run(
        s: ReminderScheduler,
        minutes: Int,
    ): List<Int> =
        (1..minutes).filter {
            clock.advanceMinutes(1.0)
            s.tick() == SchedulerDecision.REMIND
        }

    @Test
    fun `zero jitter never consults the random source`() {
        val random = CountingRandom(onRead = { fail("the random source was read with zero jitter") })
        val s = makeScheduler(monday(10), random = random)
        assertEquals(listOf(45, 90), run(s, 100))
        s.snooze(5)
        s.resume()
        s.sessionCompleted()
        s.pauseFor(60)
        run(s, 2)
        assertEquals(0, random.reads)
    }

    @Test
    fun `status fallback uses the plain interval without reading the source`() {
        val config = SchedulerConfig(intervalSeconds = 30.0 * 60, intervalJitterSeconds = 10.0 * 60)
        val random = CountingRandom()
        val s = makeScheduler(monday(8), config, random) // outside active hours: no interval drawn yet
        assertNull(s.nextFireDate)
        assertEquals(0, random.reads)
        clock.now = monday(9) // inside, but no tick has run: the status falls back to now + interval
        assertEquals(SchedulerStatus.Scheduled(monday(9, 30)), s.status)
        assertEquals(0, random.reads)
        assertEquals(SchedulerDecision.NONE, s.tick())
        assertEquals(1, random.reads)
    }

    private fun at(iso: String): Instant = OffsetDateTime.parse(iso).toInstant()

    /**
     * In the Azores, daylight saving skips midnight on 2026-03-29 (00:00 becomes 01:00, UTC-1 becomes UTC+0). That day
     * starts at 01:00, and the rest of it still ends at midnight, not at 01:00 the next day.
     */
    @Test
    fun `end of today where midnight is skipped`() {
        val clock = MockClock(at("2026-03-29T12:00:00+00:00"))
        val s =
            ReminderScheduler(SchedulerConfig(), clock, MockIdle(), MockBusy(), timeZone = ZoneId.of("Atlantic/Azores"))
        assertEquals(at("2026-03-30T00:00:00+00:00"), s.endOfToday)
        clock.now = at("2026-03-28T12:00:00-01:00")
        assertEquals(at("2026-03-29T01:00:00+00:00"), s.endOfToday)
    }

    /**
     * In the Azores, daylight saving ends at 01:00 on 2026-10-25 (back to 00:00), so midnight happens twice: the day
     * before ends at the first one, and 2026-10-25, 25 hours long, ends at midnight on 2026-10-26.
     */
    @Test
    fun `end of today where midnight repeats`() {
        val clock = MockClock(at("2026-10-24T12:00:00+00:00"))
        val s =
            ReminderScheduler(SchedulerConfig(), clock, MockIdle(), MockBusy(), timeZone = ZoneId.of("Atlantic/Azores"))
        assertEquals(at("2026-10-25T00:00:00+00:00"), s.endOfToday)
        clock.now = at("2026-10-25T00:30:00-01:00") // the second 00:30
        assertEquals(at("2026-10-26T00:00:00-01:00"), s.endOfToday)
    }
}
