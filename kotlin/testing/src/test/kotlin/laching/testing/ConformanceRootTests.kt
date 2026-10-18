package laching.testing

import kotlinx.serialization.json.JsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class ConformanceRootTests {
    @Test
    fun `finds the conformance folder above the module`() {
        val root = ConformanceRoot.find()
        assertEquals("conformance", root.fileName.toString())
        val vectors = ConformanceRoot.files("scheduler")
        assertTrue(vectors.isNotEmpty())
        assertTrue(vectors.all { it.fileName.toString().endsWith(".json") })
        assertTrue(ConformanceRoot.load(vectors.first()) is JsonObject)
    }

    @Test
    fun `the mocks do what tests need`() {
        val clock = MockClock(java.time.Instant.EPOCH)
        clock.advanceMinutes(2.0)
        assertEquals(120L, clock.now.epochSecond)
        val random = ScriptedRandom(listOf(0.25, 0.75))
        assertEquals(0.25, random.nextUnit())
        assertEquals(0.75, random.nextUnit())
        assertEquals(2, random.reads)
    }

    @Test
    fun `a scripted random needs a value`() {
        val error = assertFailsWith<IllegalArgumentException> { ScriptedRandom(emptyList()) }
        assertEquals("ScriptedRandom needs at least one value", error.message)
    }
}
