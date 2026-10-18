package laching.core.scheduling

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import laching.core.random.SplitMix64
import laching.testing.ConformanceRoot
import laching.testing.ConformanceSupport
import laching.testing.MockBusy
import laching.testing.MockClock
import laching.testing.MockIdle
import org.junit.jupiter.api.DynamicTest
import org.junit.jupiter.api.TestFactory
import java.nio.file.Path
import java.time.Instant
import java.time.ZoneId
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Replays every `conformance/scheduler/` vector the Swift reference wrote: the same clock, idle and busy inputs must give
 * the same decisions and the same state after every step.
 */
class SchedulerConformanceTests {
    @TestFactory
    fun `every scheduler vector replays`(): List<DynamicTest> {
        val files = ConformanceRoot.files("scheduler")
        assertTrue(files.size >= 40, "expected the scheduler vectors, found ${files.size}")
        return files.map { file -> DynamicTest.dynamicTest(file.fileName.toString()) { replay(file) } }
    }

    private fun replay(file: Path) {
        val vector = ConformanceRoot.load(file).jsonObject
        val zone = ConformanceSupport.timeZone(vector.string("timeZone"))
        val clock = MockClock(ConformanceSupport.date(vector.string("start")))
        val idle = MockIdle()
        val busy = MockBusy()
        val seed = vector["seed"]?.jsonPrimitive?.longOrNull?.toULong() ?: 0uL
        val scheduler =
            ReminderScheduler(
                config = config(vector["config"]!!.jsonObject),
                clock = clock,
                idle = idle,
                busy = busy,
                timeZone = zone,
                random = SplitMix64(seed),
            )
        assertState(vector["initialState"]!!.jsonObject, scheduler, clock, zone, "${file.fileName}: initial state")
        vector["steps"]!!.jsonArray.forEachIndexed { index, element ->
            val step = element.jsonObject
            val op = step.string("op")
            val where = "${file.fileName} step ${index + 1} ($op)"
            val expect = step["expect"]!!.jsonObject
            when (op) {
                "ticks" -> {
                    idle.idle = step["idleSeconds"]?.jsonPrimitive?.double ?: 0.0
                    busy.reasons.clear()
                    step["busy"]?.jsonArray?.forEach {
                        busy.reasons.add(
                            ConformanceSupport.parseBusy(it.jsonPrimitive.content),
                        )
                    }
                    val every = step["everySeconds"]?.jsonPrimitive?.double ?: 0.0
                    val decisions = mutableListOf<Pair<Int, String>>()
                    for (tick in 1..step["count"]!!.jsonPrimitive.int) {
                        clock.advanceSeconds(every)
                        val decision = scheduler.tick()
                        if (decision != SchedulerDecision.NONE) decisions.add(tick to decisionName(decision))
                    }
                    val expected =
                        expect["decisions"]?.jsonArray.orEmpty().map {
                            it.jsonObject["tick"]!!.jsonPrimitive.int to it.jsonObject.string("decision")
                        }
                    assertEquals(expected, decisions, "$where: decisions")
                }

                "setTime" -> {
                    clock.now = ConformanceSupport.date(step.string("time"))
                }

                "snooze" -> {
                    scheduler.snooze(step["minutes"]!!.jsonPrimitive.int)
                }

                "snoozeFor" -> {
                    scheduler.snoozeFor(step["seconds"]!!.jsonPrimitive.double)
                }

                "snoozeUntilAfterCall" -> {
                    scheduler.snoozeUntilAfterCall()
                }

                "pauseFor" -> {
                    scheduler.pauseFor(step["seconds"]!!.jsonPrimitive.long)
                }

                "pauseUntil" -> {
                    scheduler.pauseUntil(ConformanceSupport.date(step.string("time")))
                }

                "pauseUntilEndOfToday" -> {
                    scheduler.pauseUntilEndOfToday()
                }

                "resume" -> {
                    scheduler.resume()
                }

                "sessionCompleted" -> {
                    scheduler.sessionCompleted()
                }

                "updateConfig" -> {
                    scheduler.update(config(step["config"]!!.jsonObject))
                }

                "reminderDelivered" -> {
                    scheduler.reminderDelivered()
                }

                "restore" -> {
                    scheduler.restore(ConformanceSupport.date(step.string("time")))
                }

                "isWithinActiveHours" -> {
                    val result = scheduler.isWithinActiveHours(ConformanceSupport.date(step.string("time")))
                    assertEquals(expect["result"]!!.jsonPrimitive.boolean, result, "$where: result")
                }

                else -> {
                    error("$where: unknown op")
                }
            }
            assertState(expect["state"]!!.jsonObject, scheduler, clock, zone, "$where: state")
        }
    }

    private fun assertState(
        expected: JsonObject,
        scheduler: ReminderScheduler,
        clock: MockClock,
        zone: ZoneId,
        where: String,
    ) {
        assertEquals(snapshot(expected), snapshot(scheduler, clock, zone), where)
    }

    /** The state as the vectors write it, from either side, so one `assertEquals` shows every difference. */
    private fun snapshot(state: JsonObject): Map<String, Any?> {
        val status = state["status"]!!.jsonObject
        return mapOf(
            "now" to state.string("now"),
            "status.type" to status.string("type"),
            "status.date" to status["date"]?.jsonPrimitive?.contentOrNull,
            "status.reason" to status["reason"]?.jsonPrimitive?.contentOrNull,
            "nextFireDate" to state["nextFireDate"]?.jsonPrimitive?.contentOrNull,
            "pausedUntil" to state["pausedUntil"]?.jsonPrimitive?.contentOrNull,
            "isAway" to state["isAway"]!!.jsonPrimitive.boolean,
            "isHolding" to state["isHolding"]!!.jsonPrimitive.boolean,
            "isWaitingForCall" to state["isWaitingForCall"]!!.jsonPrimitive.boolean,
            "endOfToday" to state.string("endOfToday"),
        )
    }

    private fun snapshot(
        scheduler: ReminderScheduler,
        clock: MockClock,
        zone: ZoneId,
    ): Map<String, Any?> {
        fun iso(date: Instant?): String? = date?.let { ConformanceSupport.iso(it, zone) }
        val status = scheduler.status
        return mapOf(
            "now" to iso(clock.now),
            "status.type" to
                when (status) {
                    is SchedulerStatus.Scheduled -> "scheduled"
                    is SchedulerStatus.Paused -> "paused"
                    SchedulerStatus.OutsideActiveHours -> "outsideActiveHours"
                    SchedulerStatus.Away -> "away"
                    is SchedulerStatus.Postponed -> "postponed"
                },
            "status.date" to
                when (status) {
                    is SchedulerStatus.Scheduled -> iso(status.date)
                    is SchedulerStatus.Paused -> iso(status.until)
                    else -> null
                },
            "status.reason" to (status as? SchedulerStatus.Postponed)?.let { ConformanceSupport.busyName(it.reason) },
            "nextFireDate" to iso(scheduler.nextFireDate),
            "pausedUntil" to iso(scheduler.pausedUntil),
            "isAway" to scheduler.isAway,
            "isHolding" to scheduler.isHolding,
            "isWaitingForCall" to scheduler.isWaitingForCall,
            "endOfToday" to iso(scheduler.endOfToday),
        )
    }

    private fun config(json: JsonObject): SchedulerConfig =
        SchedulerConfig(
            intervalSeconds = json["intervalSeconds"]!!.jsonPrimitive.double,
            activeStartMinute = json["activeStartMinute"]!!.jsonPrimitive.int,
            activeEndMinute = json["activeEndMinute"]!!.jsonPrimitive.int,
            activeWeekdays = json["activeWeekdays"]!!.jsonArray.map { it.jsonPrimitive.int }.toSet(),
            quietHours = json["quietHours"]!!.jsonArray.map { it.jsonPrimitive.int }.toSet(),
            idleThresholdSeconds = json["idleThresholdSeconds"]!!.jsonPrimitive.double,
            postponeDuringCalls = json["postponeDuringCalls"]!!.jsonPrimitive.boolean,
            postponeDuringFullScreen = json["postponeDuringFullScreen"]!!.jsonPrimitive.boolean,
            busyGraceSeconds = json["busyGraceSeconds"]!!.jsonPrimitive.double,
            afterCallFallbackSeconds = json["afterCallFallbackSeconds"]!!.jsonPrimitive.double,
            intervalJitterSeconds = json["intervalJitterSeconds"]?.jsonPrimitive?.doubleOrNull ?: 0.0,
        )

    private fun decisionName(decision: SchedulerDecision): String =
        when (decision) {
            SchedulerDecision.NONE -> "none"
            SchedulerDecision.REMIND -> "remind"
            SchedulerDecision.POSTPONE -> "postpone"
        }

    private fun JsonObject.string(key: String): String = this[key]!!.jsonPrimitive.content
}
