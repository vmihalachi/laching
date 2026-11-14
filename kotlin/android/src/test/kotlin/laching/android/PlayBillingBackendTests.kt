package laching.android

import android.app.Activity
import android.content.SharedPreferences
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.AcknowledgePurchaseResponseListener
import com.android.billingclient.api.AlternativeBillingOnlyAvailabilityListener
import com.android.billingclient.api.AlternativeBillingOnlyInformationDialogListener
import com.android.billingclient.api.AlternativeBillingOnlyReportingDetailsListener
import com.android.billingclient.api.BillingChoiceInfoResponseListener
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClient.BillingResponseCode
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingConfigResponseListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingProgramAvailabilityListener
import com.android.billingclient.api.BillingProgramInformationDialogListener
import com.android.billingclient.api.BillingProgramInformationDialogParams
import com.android.billingclient.api.BillingProgramReportingDetailsListener
import com.android.billingclient.api.BillingProgramReportingDetailsParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.ConsumeParams
import com.android.billingclient.api.ConsumeResponseListener
import com.android.billingclient.api.ExternalOfferAvailabilityListener
import com.android.billingclient.api.ExternalOfferInformationDialogListener
import com.android.billingclient.api.ExternalOfferReportingDetailsListener
import com.android.billingclient.api.GetBillingChoiceInfoParams
import com.android.billingclient.api.GetBillingConfigParams
import com.android.billingclient.api.InAppMessageParams
import com.android.billingclient.api.InAppMessageResponseListener
import com.android.billingclient.api.LaunchExternalLinkParams
import com.android.billingclient.api.LaunchExternalLinkResponseListener
import com.android.billingclient.api.ProductDetailsResponseListener
import com.android.billingclient.api.PurchasesResponseListener
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import laching.core.settings.InMemorySettingsStore
import laching.core.store.ProductCatalog
import laching.core.store.PurchaseStore
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * What [PlayBillingBackend.entitledProductIds] answers when Play reads and when it can't, and what the PurchaseStore
 * over it keeps: over a stand-in Play and preferences in memory. Internal because :android compiles its tests in
 * explicit API mode too.
 */
@OptIn(ExperimentalCoroutinesApi::class)
internal class PlayBillingBackendTests {
    private val logged = mutableListOf<String>()
    private val preferences = MemoryPreferences()

    /** The backend's scope runs on Main, which a JVM test has only when it sets one. */
    @BeforeTest
    fun setMain() = Dispatchers.setMain(UnconfinedTestDispatcher())

    @AfterTest
    fun resetMain() = Dispatchers.resetMain()

    private fun backend(play: FakePlay) =
        PlayBillingBackend(preferences, ProductCatalog(listOf(SUPPORTER)), { null }, { logged += it }, { play })

    /** What an earlier read that worked left, under the key the backend caches what's owned in. */
    private var cache: Set<String>?
        get() = preferences.getStringSet("owned", null)
        set(value) = preferences.edit().putStringSet("owned", value).apply()

    /** Offline, or no Play on the device: the last read that worked, never an empty set that locks what was bought. */
    @Test
    fun `when Play can't be reached it answers the cache and logs it`() =
        runTest {
            cache = setOf(SUPPORTER)
            val owned = backend(FakePlay(setup = BillingResponseCode.SERVICE_UNAVAILABLE)).entitledProductIds()
            assertEquals(setOf(SUPPORTER), owned)
            assertTrue("Connecting to Play failed" in logged.single(), logged.single())
        }

    @Test
    fun `when Play refuses the read it answers the cache and keeps it`() =
        runTest {
            cache = setOf(SUPPORTER)
            val owned = backend(FakePlay(purchases = BillingResponseCode.ERROR)).entitledProductIds()
            assertEquals(setOf(SUPPORTER), owned)
            assertEquals(setOf(SUPPORTER), cache)
            assertTrue("Reading purchases failed" in logged.single(), logged.single())
        }

    /** An empty cache knows nothing, before any read has worked or after one that found nothing: the read throws. */
    @Test
    fun `when Play can't be read and the cache is empty it throws`() =
        runTest {
            for (cached in listOf(null, emptySet<String>())) {
                cache = cached
                assertFailsWith<PlayBillingBackend.BillingException>("cache $cached") {
                    backend(FakePlay(purchases = BillingResponseCode.ERROR)).entitledProductIds()
                }
                assertEquals(cached, cache)
            }
            assertTrue(logged.isEmpty(), logged.toString())
        }

    /**
     * A reinstall that kept the app's settings but not Play's cache, offline: the store keeps the supporter it knew,
     * where an empty answer would have locked them out.
     */
    @Test
    fun `the store keeps what it knew when Play can't be read and the cache is empty`() =
        runTest {
            val store = store(backend(FakePlay(setup = BillingResponseCode.SERVICE_UNAVAILABLE)), owned = SUPPORTER)
            store.refreshEntitlements()
            assertEquals(setOf(SUPPORTER), store.owned)
        }

    /** A read that works and finds nothing still means nothing is owned, so a refund shows in the store. */
    @Test
    fun `the store takes an empty read that works as owning nothing`() =
        runTest {
            val store = store(backend(FakePlay()), owned = SUPPORTER)
            store.refreshEntitlements()
            assertEquals(emptySet(), store.owned)
            assertEquals(emptySet(), cache)
        }

    private fun TestScope.store(
        backend: PlayBillingBackend,
        owned: String,
    ) = PurchaseStore(
        ProductCatalog(listOf(SUPPORTER)),
        backend,
        InMemorySettingsStore().apply { setStrings("laching.ownedProducts", listOf(owned)) },
        this,
    )

    /** Play's answer wins over the cache and replaces it, so a refund shows. */
    @Test
    fun `a read that works is the answer and the new cache`() =
        runTest {
            cache = setOf(SUPPORTER)
            val owned = backend(FakePlay()).entitledProductIds()
            assertEquals(emptySet(), owned)
            assertEquals(emptySet(), cache)
            assertTrue(logged.isEmpty(), logged.toString())
        }

    /** Only Play's own failures fall back: a cancelled read stays cancelled and isn't logged as one. */
    @Test
    fun `a cancelled read is not taken for a failed one`() =
        runTest {
            cache = setOf(SUPPORTER)
            val read = async { backend(FakePlay(purchases = null)).entitledProductIds() }
            runCurrent()
            read.cancel()
            assertFailsWith<CancellationException> { read.await() }
            assertTrue(logged.isEmpty(), logged.toString())
        }

    private companion object {
        const val SUPPORTER = "test.supporter"
    }
}

private fun unused(): Nothing = error("Not used by these tests")

private fun result(code: Int): BillingResult =
    BillingResult
        .newBuilder()
        .setResponseCode(code)
        .setDebugMessage("from the test")
        .build()

/**
 * Play as these tests need it: [setup] answers the connection and [purchases] the read of what's bought, with nothing
 * bought (null: it never answers). Nothing else is called. A Play Billing update that adds an abstract method to
 * [BillingClient] needs one more `unused()` override here.
 */
@Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
private class FakePlay(
    private val setup: Int = BillingResponseCode.OK,
    private val purchases: Int? = BillingResponseCode.OK,
) : BillingClient() {
    private var ready = false

    override fun isReady(): Boolean = ready

    override fun startConnection(listener: BillingClientStateListener) {
        ready = setup == BillingResponseCode.OK
        listener.onBillingSetupFinished(result(setup))
    }

    override fun queryPurchasesAsync(
        params: QueryPurchasesParams,
        listener: PurchasesResponseListener,
    ) {
        val code = purchases ?: return
        listener.onQueryPurchasesResponse(result(code), emptyList())
    }

    override fun getConnectionState(): Int = unused()

    override fun isFeatureSupported(feature: String): BillingResult = unused()

    override fun launchBillingFlow(
        activity: Activity,
        params: BillingFlowParams,
    ): BillingResult = unused()

    override fun showAlternativeBillingOnlyInformationDialog(
        activity: Activity,
        listener: AlternativeBillingOnlyInformationDialogListener,
    ): BillingResult = unused()

    override fun showExternalOfferInformationDialog(
        activity: Activity,
        listener: ExternalOfferInformationDialogListener,
    ): BillingResult = unused()

    override fun showInAppMessages(
        activity: Activity,
        params: InAppMessageParams,
        listener: InAppMessageResponseListener,
    ): BillingResult = unused()

    override fun acknowledgePurchase(
        params: AcknowledgePurchaseParams,
        listener: AcknowledgePurchaseResponseListener,
    ): Unit = unused()

    override fun consumeAsync(
        params: ConsumeParams,
        listener: ConsumeResponseListener,
    ): Unit = unused()

    override fun createAlternativeBillingOnlyReportingDetailsAsync(
        listener: AlternativeBillingOnlyReportingDetailsListener,
    ): Unit = unused()

    override fun createBillingProgramReportingDetailsAsync(
        params: BillingProgramReportingDetailsParams,
        listener: BillingProgramReportingDetailsListener,
    ): Unit = unused()

    override fun createExternalOfferReportingDetailsAsync(listener: ExternalOfferReportingDetailsListener): Unit =
        unused()

    override fun endConnection(): Unit = unused()

    override fun getBillingChoiceInfoAsync(
        params: GetBillingChoiceInfoParams,
        listener: BillingChoiceInfoResponseListener,
    ): Unit = unused()

    override fun getBillingConfigAsync(
        params: GetBillingConfigParams,
        listener: BillingConfigResponseListener,
    ): Unit = unused()

    override fun isAlternativeBillingOnlyAvailableAsync(listener: AlternativeBillingOnlyAvailabilityListener): Unit =
        unused()

    override fun isBillingProgramAvailableAsync(
        program: Int,
        listener: BillingProgramAvailabilityListener,
    ): Unit = unused()

    override fun isExternalOfferAvailableAsync(listener: ExternalOfferAvailabilityListener): Unit = unused()

    override fun launchExternalLink(
        activity: Activity,
        params: LaunchExternalLinkParams,
        listener: LaunchExternalLinkResponseListener,
    ): Unit = unused()

    override fun queryProductDetailsAsync(
        params: QueryProductDetailsParams,
        listener: ProductDetailsResponseListener,
    ): Unit = unused()

    override fun showBillingProgramInformationDialog(
        activity: Activity,
        params: BillingProgramInformationDialogParams,
        listener: BillingProgramInformationDialogListener,
    ): Unit = unused()
}

/** SharedPreferences in memory, string sets only: all the backend keeps. */
private class MemoryPreferences : SharedPreferences {
    private val stored = HashMap<String, Set<String>>()

    override fun getStringSet(
        key: String,
        defValues: Set<String>?,
    ): Set<String>? = stored[key] ?: defValues

    override fun contains(key: String): Boolean = key in stored

    override fun getAll(): Map<String, *> = stored.toMap()

    override fun edit(): SharedPreferences.Editor = Editor()

    override fun getString(
        key: String,
        defValue: String?,
    ): String? = unused()

    override fun getInt(
        key: String,
        defValue: Int,
    ): Int = unused()

    override fun getLong(
        key: String,
        defValue: Long,
    ): Long = unused()

    override fun getFloat(
        key: String,
        defValue: Float,
    ): Float = unused()

    override fun getBoolean(
        key: String,
        defValue: Boolean,
    ): Boolean = unused()

    override fun registerOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener,
    ): Unit = unused()

    override fun unregisterOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener,
    ): Unit = unused()

    private inner class Editor : SharedPreferences.Editor {
        private val changes = HashMap<String, Set<String>?>()

        override fun putStringSet(
            key: String,
            values: Set<String>?,
        ): SharedPreferences.Editor {
            changes[key] = values?.toSet()
            return this
        }

        override fun remove(key: String): SharedPreferences.Editor {
            changes[key] = null
            return this
        }

        override fun apply() {
            for ((key, value) in changes) if (value == null) stored.remove(key) else stored[key] = value
        }

        override fun commit(): Boolean {
            apply()
            return true
        }

        override fun putString(
            key: String,
            value: String?,
        ): SharedPreferences.Editor = unused()

        override fun putInt(
            key: String,
            value: Int,
        ): SharedPreferences.Editor = unused()

        override fun putLong(
            key: String,
            value: Long,
        ): SharedPreferences.Editor = unused()

        override fun putFloat(
            key: String,
            value: Float,
        ): SharedPreferences.Editor = unused()

        override fun putBoolean(
            key: String,
            value: Boolean,
        ): SharedPreferences.Editor = unused()

        override fun clear(): SharedPreferences.Editor = unused()
    }
}
