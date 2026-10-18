package laching.core.scheduling

import laching.core.random.RandomSource
import laching.core.random.SystemRandom
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/**
 * Decides when to remind, from the clock, the idle time and the busy state the app injects. Pure: no timers, no UI;
 * the app calls [tick] once a second or so and acts on the decision. Ported from `Laching.Core/Scheduling` and
 * checked against the Swift reference by `conformance/scheduler`.
 *
 * Not thread-safe: call it from one thread (the app's main thread).
 */
public class ReminderScheduler(
    config: SchedulerConfig,
    private val clock: Clock,
    private val idle: IdleTimeProvider,
    private val busy: BusyStateProvider = NeverBusy,
    /** The zone whose calendar decides days, weekdays and the active window. */
    public val timeZone: ZoneId = ZoneId.systemDefault(),
    private val random: RandomSource = SystemRandom(),
) {
    public var config: SchedulerConfig = config
        private set

    /** When the next reminder fires; null while paused, outside the active hours or not yet computed. */
    public var nextFireDate: Instant? = null
        private set

    public var pausedUntil: Instant? = null
        private set

    /** The user has been idle past the threshold; the timer restarts when they are back. */
    public var isAway: Boolean = false
        private set

    /** A due reminder is held while the user is busy. */
    public var isHolding: Boolean = false
        private set

    /** The user asked to be reminded after the current call. */
    public var isAfterCallArmed: Boolean = false
        private set

    /** Armed for after the call and the call hasn't ended yet. */
    public val isWaitingForCall: Boolean get() = isAfterCallArmed && !isHolding

    public val isInCall: Boolean get() = BusyReason.CALL in busy.currentBusyReasons()

    /** A reminder was shown and the user hasn't acted on it yet. */
    public var hasUnansweredReminder: Boolean = false
        private set

    private var lastBusyAt: Instant? = null
    private var lastBusyReason: BusyReason = BusyReason.CALL

    init {
        val now = clock.now
        if (isWithinActiveHours(now)) nextFireDate = now.plusSeconds(nextInterval())
    }

    public val status: SchedulerStatus
        get() {
            val now = clock.now
            val paused = pausedUntil
            if (paused != null && now < paused) return SchedulerStatus.Paused(paused)
            if (!isWithinActiveHours(now)) return SchedulerStatus.OutsideActiveHours
            if (isHolding) return SchedulerStatus.Postponed(lastBusyReason)
            if (isAway) return SchedulerStatus.Away
            return SchedulerStatus.Scheduled(nextFireDate ?: now.plusSeconds(config.intervalSeconds))
        }

    /** Local midnight that ends the current day, for "pause until the end of today". */
    public val endOfToday: Instant get() = addLocalDays(startOfDay(clock.now), 1)

    /** A new configuration; a changed interval restarts the timer, a kept one leaves the next reminder where it was. */
    public fun update(config: SchedulerConfig) {
        val intervalChanged = config.intervalSeconds != this.config.intervalSeconds
        this.config = config
        val now = clock.now
        if (intervalChanged || nextFireDate == null) {
            clearBusyState()
            nextFireDate = if (isWithinActiveHours(now)) now.plusSeconds(nextInterval()) else null
        }
    }

    public fun isWithinActiveHours(date: Instant): Boolean {
        val local = date.atZone(timeZone)
        val weekday = local.dayOfWeek.value % 7 + 1 // java: Monday = 1 … Sunday = 7; here Sunday = 1 … Saturday = 7
        if (weekday !in config.activeWeekdays) return false
        if (local.hour in config.quietHours) return false
        val minute = local.hour * 60 + local.minute
        val start = config.activeStartMinute
        val end = config.activeEndMinute
        return if (start <= end) minute >= start && minute < end else minute >= start || minute < end
    }

    /** One step of the timer: what to do right now. */
    public fun tick(): SchedulerDecision {
        val now = clock.now
        val paused = pausedUntil
        if (paused != null) {
            if (now < paused) return SchedulerDecision.NONE
            pausedUntil = null
            nextFireDate = now.plusSeconds(nextInterval())
        }
        if (!isWithinActiveHours(now)) {
            nextFireDate = null
            isAway = false
            clearBusyState()
            return SchedulerDecision.NONE
        }
        val fire = nextFireDate
        if (fire == null) {
            nextFireDate = now.plusSeconds(nextInterval())
            return SchedulerDecision.NONE
        }
        val watched = config.postponeReasons.toMutableSet()
        if (isAfterCallArmed) watched.add(BusyReason.CALL)
        val reasons = busy.currentBusyReasons().intersect(watched)
        if (reasons.isNotEmpty()) {
            lastBusyAt = now
            lastBusyReason = if (BusyReason.CALL in reasons) BusyReason.CALL else BusyReason.FULL_SCREEN
            isAway = false
            if ((isWaitingForCall && BusyReason.CALL in reasons) || now >= fire) isHolding = true
            return if (isHolding) SchedulerDecision.POSTPONE else SchedulerDecision.NONE
        }
        if (idle.secondsSinceLastInput() >= config.idleThresholdSeconds) {
            isAway = true
            clearBusyState()
            nextFireDate = now.plusSeconds(nextInterval())
            return SchedulerDecision.NONE
        }
        isAway = false
        if (isHolding) {
            val last = lastBusyAt
            if (last != null && Duration.between(last, now).toNanos() / 1e9 < config.busyGraceSeconds) {
                return SchedulerDecision.POSTPONE
            }
            return fireReminder(now)
        }
        return if (now >= fire) fireReminder(now) else SchedulerDecision.NONE
    }

    /**
     * Push the reminder back by [minutes]. Answering a reminder (shown, held or armed for after a call) reminds in
     * exactly that long; a snooze with nothing pending never pulls the next reminder closer.
     */
    public fun snooze(minutes: Int = 10): Unit = snoozeFor(minutes * 60.0)

    /** [snooze] for a duration in seconds, for a snooze that isn't whole minutes (say 90 seconds). */
    public fun snoozeFor(seconds: Double) {
        val snoozed = clock.now.plusSeconds(seconds)
        val answering = hasUnansweredReminder || isHolding || isAfterCallArmed
        pausedUntil = null
        clearBusyState()
        nextFireDate = if (answering) snoozed else maxOf(nextFireDate ?: snoozed, snoozed)
    }

    /** Remind when the current call ends, or after [SchedulerConfig.afterCallFallbackSeconds] if it never does. */
    public fun snoozeUntilAfterCall() {
        pausedUntil = null
        clearBusyState()
        isAfterCallArmed = true
        nextFireDate = clock.now.plusSeconds(config.afterCallFallbackSeconds)
    }

    public fun pauseFor(seconds: Long): Unit = pauseUntil(clock.now.plusSeconds(seconds))

    public fun pauseUntil(date: Instant) {
        pausedUntil = date
        clearBusyState()
        nextFireDate = null
    }

    public fun pauseUntilEndOfToday(): Unit = pauseUntil(endOfToday)

    public fun resume() {
        pausedUntil = null
        clearBusyState()
        nextFireDate = clock.now.plusSeconds(nextInterval())
    }

    /** The user did the exercise: the timer starts over. */
    public fun sessionCompleted() {
        clearBusyState()
        nextFireDate = clock.now.plusSeconds(nextInterval())
    }

    /** Puts back a next fire date saved before a relaunch, when it is still ahead and nothing else is going on. */
    public fun restore(date: Instant) {
        val now = clock.now
        if (pausedUntil != null || !isWithinActiveHours(now) || date <= now) return
        nextFireDate = date
    }

    /** The app showed the reminder; a snooze now counts as answering it. */
    public fun reminderDelivered() {
        hasUnansweredReminder = true
    }

    /**
     * Up to [limit] reminder dates in `(from, until]`, as [tick] would fire them with no idle and no busy input:
     * the next one as it stands (or one interval from [from] when none is set or it has passed), then one interval
     * after each, skipping ahead over quiet hours, inactive hours and days, and a [pausedUntil] that ends in the
     * range. The scheduler is left as it was, except that a jittered config draws the intervals from its random
     * source. An app that doesn't run between reminders (a phone) schedules them ahead from this, and reports what
     * the system delivered with [reminderDelivered].
     */
    public fun upcomingReminders(
        from: Instant,
        until: Instant,
        limit: Int = 64,
    ): List<Instant> {
        if (limit <= 0 || until <= from || config.intervalSeconds <= 0 ||
            config.activeWeekdays.isEmpty()
        ) {
            return emptyList()
        }
        var cursor = from
        var paused = pausedUntil
        var fire: Instant? = null
        if (paused == null && isWithinActiveHours(from)) {
            fire = nextFireDate?.takeIf { it > from } ?: from.plusSeconds(nextInterval())
        }

        val results = ArrayList<Instant>(min(limit, 64))
        while (results.size < limit && cursor <= until) {
            val pause = paused
            if (pause != null) {
                if (cursor < pause) {
                    if (pause > until) break
                    cursor = pause
                }
                paused = null
                fire = if (isWithinActiveHours(cursor)) cursor.plusSeconds(nextInterval()) else null
                continue
            }

            if (!isWithinActiveHours(cursor)) {
                val nextActive = firstMinute(cursor, until) { isWithinActiveHours(it) } ?: break
                cursor = nextActive
                fire = cursor.plusSeconds(nextInterval())
                continue
            }

            val target = fire ?: cursor.plusSeconds(nextInterval())
            fire = target

            val inactive = firstInactiveDate(cursor, minOf(target, until))
            if (inactive != null) {
                fire = null
                cursor = inactive
                continue
            }

            if (target > until) break
            results.add(target)
            cursor = target
            fire = target.plusSeconds(nextInterval())
        }
        return results
    }

    /** The first whole minute after [date]. */
    private fun nextMinuteBoundary(date: Instant): Instant = Instant.ofEpochSecond((date.epochSecond / 60 + 1) * 60)

    /** The first whole minute after [after], up to [upTo], where [test] holds. */
    private fun firstMinute(
        after: Instant,
        upTo: Instant,
        test: (Instant) -> Boolean,
    ): Instant? {
        var probe = nextMinuteBoundary(after)
        while (probe <= upTo) {
            if (test(probe)) return probe
            probe = probe.plusSeconds(60)
        }
        return null
    }

    /** The first inactive minute after [after], or [upTo] itself when it is inactive. */
    private fun firstInactiveDate(
        after: Instant,
        upTo: Instant,
    ): Instant? = firstMinute(after, upTo) { !isWithinActiveHours(it) } ?: upTo.takeUnless { isWithinActiveHours(it) }

    private fun fireReminder(now: Instant): SchedulerDecision {
        clearBusyState()
        hasUnansweredReminder = true
        nextFireDate = now.plusSeconds(nextInterval())
        return SchedulerDecision.REMIND
    }

    private fun clearBusyState() {
        isHolding = false
        isAfterCallArmed = false
        lastBusyAt = null
        hasUnansweredReminder = false
    }

    /** The interval, jittered by a whole number of seconds when the config asks for it. */
    private fun nextInterval(): Double {
        val interval = config.intervalSeconds
        if (config.intervalJitterSeconds > 0) {
            val jitter = min(config.intervalJitterSeconds, max(interval - 1, 0.0))
            val unit = random.nextUnit()
            return interval + floor((unit * 2 - 1) * jitter + 0.5)
        }
        return interval
    }

    // Local-time arithmetic in [timeZone]. A local time inside a DST gap moves forward minute by minute until it
    // exists; an ambiguous one (fall back) takes its earlier instant, as the Swift reference does.

    private fun atLocal(local: LocalDateTime): Instant {
        val rules = timeZone.rules
        var candidate = local
        while (rules.getValidOffsets(candidate).isEmpty()) candidate = candidate.plusMinutes(1)
        val offset = rules.getValidOffsets(candidate).maxBy { it.totalSeconds }
        return candidate.toInstant(offset)
    }

    private fun startOfDay(date: Instant): Instant = atLocal(date.atZone(timeZone).toLocalDate().atStartOfDay())

    private fun addLocalDays(
        date: Instant,
        days: Long,
    ): Instant = atLocal(date.atZone(timeZone).toLocalDateTime().plusDays(days))
}
