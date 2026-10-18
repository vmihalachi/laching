package laching.core.scheduling

import laching.core.random.RandomSource
import laching.testing.CountingRandom
import laching.testing.MockBusy
import laching.testing.MockClock
import laching.testing.MockIdle
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.fail

/** When [ReminderScheduler] reads its random source, as `ReminderSchedulerTests.swift` and `.cs` check it. */
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
}
