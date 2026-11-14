package laching.core.store

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import laching.core.settings.InMemorySettingsStore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

@OptIn(ExperimentalCoroutinesApi::class)
class PurchaseStoreTests {
    private val supporter = "test.supporter"
    private val smallTip = "test.tip.small"
    private val mediumTip = "test.tip.medium"
    private val catalog = ProductCatalog(listOf(supporter), listOf(smallTip, mediumTip))
    private val settings = InMemorySettingsStore()
    private val backend = MockStoreBackend(catalog)

    private fun TestScope.store(
        cacheKey: String = "test.owned",
        catalog: ProductCatalog = this@PurchaseStoreTests.catalog,
    ) = PurchaseStore(catalog, backend, settings, this, cacheKey)

    @Test
    fun `the catalog knows its products`() {
        assertEquals(listOf(supporter, smallTip, mediumTip), catalog.all)
        assertTrue(catalog.isUnlockable(supporter))
        assertFalse(catalog.isUnlockable(smallTip))
        assertTrue(catalog.isTip(smallTip))
        assertFalse(catalog.isTip(supporter))
    }

    @Test
    fun `buying an unlockable owns it`() =
        runTest {
            val store = store()
            store.start()
            assertFalse(store.isOwned(supporter))
            store.buy(supporter)
            assertTrue(store.isOwned(supporter))
            assertEquals("€${supporter.length}", store.price(supporter))
            assertEquals(LoadState.LOADED, store.loadState)
            assertEquals(listOf(supporter), backend.purchased)
            assertFalse(store.justTipped)
            assertNull(store.error)
        }

    @Test
    fun `ownership is cached in the settings`() =
        runTest {
            val store = store()
            store.start()
            store.buy(supporter)
            assertEquals(listOf(supporter), settings.getStrings("test.owned"))
            assertTrue(store().isOwned(supporter))
            assertFalse(store(cacheKey = "other").isOwned(supporter))
        }

    @Test
    fun `a refund takes the unlockable away`() =
        runTest {
            val store = store()
            store.start()
            store.buy(supporter)
            backend.owned.clear()
            backend.send(TransactionUpdate(supporter, isRevoked = true))
            advanceUntilIdle()
            assertFalse(store.isOwned(supporter))
        }

    @Test
    fun `a licenses-changed update re-reads ownership`() =
        runTest {
            val store = store()
            store.start()
            backend.owned += supporter
            backend.send(TransactionUpdate("", isRevoked = false))
            advanceUntilIdle()
            assertTrue(store.isOwned(supporter))
            assertFalse(store.justTipped)
        }

    @Test
    fun `tips unlock nothing but say thanks`() =
        runTest {
            val store = store()
            store.start()
            store.tip(smallTip)
            assertFalse(store.isOwned(smallTip))
            assertTrue(store.justTipped)
            store.acknowledgeTip()
            assertFalse(store.justTipped)
        }

    @Test
    fun `a tip arriving from the store thanks unless revoked`() =
        runTest {
            val store = store()
            store.start()
            backend.send(TransactionUpdate(smallTip, isRevoked = true))
            advanceUntilIdle()
            assertFalse(store.justTipped)
            backend.send(TransactionUpdate(smallTip, isRevoked = false))
            advanceUntilIdle()
            assertTrue(store.justTipped)
        }

    @Test
    fun `a cancelled purchase changes nothing`() =
        runTest {
            val store = store()
            store.start()
            backend.nextOutcome = PurchaseOutcome.CANCELLED
            store.buy(supporter)
            assertFalse(store.isOwned(supporter))
            assertNull(store.error)
            assertFalse(store.isPending)
        }

    @Test
    fun `a pending purchase completes later`() =
        runTest {
            val store = store()
            store.start()
            backend.nextOutcome = PurchaseOutcome.PENDING
            store.buy(supporter)
            assertTrue(store.isPending)
            assertFalse(store.isOwned(supporter))
            backend.owned += supporter
            backend.send(TransactionUpdate(supporter, isRevoked = false))
            advanceUntilIdle()
            assertFalse(store.isPending)
            assertTrue(store.isOwned(supporter))
        }

    @Test
    fun `a failed purchase reports an error that clears`() =
        runTest {
            val store = store()
            store.start()
            backend.failPurchase = true
            store.tip(smallTip)
            assertEquals(PurchaseError.PURCHASE_FAILED, store.error)
            assertFalse(store.justTipped)
            assertNull(store.purchasing)
            assertFalse(store.isBusy)
            store.clearError()
            assertNull(store.error)
        }

    @Test
    fun `restore asks the store and re-reads ownership`() =
        runTest {
            val store = store()
            store.start()
            backend.owned += supporter
            store.restore()
            assertEquals(1, backend.restores)
            assertTrue(store.isOwned(supporter))
            assertNull(store.error)
            assertFalse(store.isRestoring)
        }

    @Test
    fun `a failed restore reports an error`() =
        runTest {
            val store = store()
            store.start()
            backend.failRestore = true
            store.restore()
            assertEquals(PurchaseError.RESTORE_FAILED, store.error)
            assertFalse(store.isRestoring)
        }

    /** Offline at launch: the cached unlock stays (and stays cached), and prices still load. */
    @Test
    fun `a failed read at launch keeps the cached unlock`() =
        runTest {
            settings.setStrings("test.owned", listOf(supporter))
            backend.failEntitlements = true
            val store = store()
            store.start()
            assertTrue(store.isOwned(supporter))
            assertEquals(listOf(supporter), settings.getStrings("test.owned"))
            assertEquals(LoadState.LOADED, store.loadState)
            assertNull(store.error)
        }

    /** A read that fails after a purchase, or on an update, leaves what's owned; the next read that works applies. */
    @Test
    fun `a failed read after a purchase keeps what is owned`() =
        runTest {
            val store = store()
            store.start()
            store.buy(supporter)
            assertTrue(store.isOwned(supporter))

            backend.failEntitlements = true
            store.refreshEntitlements()
            backend.send(TransactionUpdate("", isRevoked = false))
            advanceUntilIdle()
            assertEquals(setOf(supporter), store.owned)
            assertEquals(listOf(supporter), settings.getStrings("test.owned"))

            // Refunded: once the store answers again, that counts.
            backend.failEntitlements = false
            backend.owned.clear()
            store.refreshEntitlements()
            assertTrue(store.owned.isEmpty())
            assertEquals(emptyList(), settings.getStrings("test.owned"))
        }

    /** An update from the store whose read fails (the observer's own re-read) leaves what's owned as it was. */
    @Test
    fun `an update whose read fails keeps what is owned`() =
        runTest {
            backend.owned += supporter
            val store = store()
            store.start()
            backend.failEntitlements = true
            backend.send(TransactionUpdate(supporter, isRevoked = true))
            advanceUntilIdle()
            assertTrue(store.isOwned(supporter))
        }

    /** The store took the payment; failing to read it back isn't a failed purchase. */
    @Test
    fun `a purchase whose read fails is not a failed purchase`() =
        runTest {
            val store = store()
            store.start()
            backend.failEntitlements = true
            store.tip(smallTip)
            assertNull(store.error)
            assertTrue(store.justTipped)
            assertNull(store.purchasing)
            assertFalse(store.isBusy)
        }

    /**
     * Buying a second unlockable when the read back fails keeps the one already owned, in the store and its cache:
     * a failed read never clears what's owned. The next read that works adds the new one.
     */
    @Test
    fun `a purchase whose read fails keeps what was owned`() =
        runTest {
            val pack = "test.pack"
            val twoUnlockables = ProductCatalog(listOf(supporter, pack), listOf(smallTip))
            val backend = MockStoreBackend(twoUnlockables)
            backend.owned += pack
            val store = PurchaseStore(twoUnlockables, backend, settings, this, "test.owned")
            store.start()
            assertEquals(setOf(pack), store.owned)

            backend.failEntitlements = true
            store.buy(supporter)
            assertEquals(listOf(supporter), backend.purchased)
            assertEquals(setOf(pack), store.owned)
            assertEquals(listOf(pack), settings.getStrings("test.owned"))
            assertNull(store.error)
            assertNull(store.purchasing)
            assertFalse(store.isBusy)

            backend.failEntitlements = false
            store.refreshEntitlements()
            assertEquals(setOf(supporter, pack), store.owned)
        }

    /** A restore that can't read what's owned afterwards says so, and keeps what was owned. */
    @Test
    fun `a restore whose read fails reports restore failed`() =
        runTest {
            backend.owned += supporter
            val store = store()
            store.start()
            backend.failEntitlements = true
            store.restore()
            assertEquals(1, backend.restores)
            assertEquals(PurchaseError.RESTORE_FAILED, store.error)
            assertFalse(store.isRestoring)
            assertTrue(store.isOwned(supporter))
        }

    @Test
    fun `the mock's failed read throws and later reads again`() =
        runTest {
            backend.owned += supporter
            backend.failEntitlements = true
            assertFailsWith<MockStoreBackend.Failure> { backend.entitledProductIds() }
            backend.failEntitlements = false
            assertEquals(setOf(supporter), backend.entitledProductIds())
        }

    @Test
    fun `unreachable products fail the load`() =
        runTest {
            backend.failProducts = true
            val store = store()
            store.start()
            assertNull(store.price(supporter))
            assertEquals(LoadState.FAILED, store.loadState)
        }

    @Test
    fun `a missing unlockable fails the load but keeps the prices that came`() =
        runTest {
            backend.missingProducts += supporter
            val store = store()
            store.start()
            assertEquals(LoadState.FAILED, store.loadState)
            assertNotNull(store.price(smallTip))
        }

    @Test
    fun `a missing tip is not a failure`() =
        runTest {
            backend.missingProducts += smallTip
            val store = store()
            store.start()
            assertEquals(LoadState.LOADED, store.loadState)
            assertNull(store.price(smallTip))
            assertNotNull(store.price(supporter))
        }

    @Test
    fun `a tips-only catalog loads when anything comes`() =
        runTest {
            val tipsOnly = ProductCatalog(emptyList(), listOf(smallTip, mediumTip))
            backend.missingProducts += smallTip
            val store = store(catalog = tipsOnly)
            store.start()
            assertEquals(LoadState.LOADED, store.loadState)
            backend.missingProducts += mediumTip
            store.loadProducts()
            assertEquals(LoadState.FAILED, store.loadState)
        }

    @Test
    fun `loading retries only until it succeeds`() =
        runTest {
            val store = store()
            assertEquals(LoadState.IDLE, store.loadState)
            backend.failProducts = true
            store.start()
            assertEquals(LoadState.FAILED, store.loadState)
            backend.failProducts = false
            store.loadProductsIfNeeded()
            assertEquals(LoadState.LOADED, store.loadState)
            backend.failProducts = true
            store.loadProductsIfNeeded()
            assertEquals(LoadState.LOADED, store.loadState)
        }

    @Test
    fun `changes are announced`() =
        runTest {
            val store = store()
            var changes = 0
            val remove = store.onChanged { changes++ }
            store.start()
            assertTrue(changes >= 2, "start: $changes")
            changes = 0
            store.buy(supporter)
            assertTrue(changes >= 2, "buy: $changes")
            changes = 0
            store.restore()
            assertTrue(changes >= 2, "restore: $changes")
            changes = 0
            store.acknowledgeTip()
            assertEquals(0, changes)
            remove()
            store.clearError()
            store.buy(smallTip)
            assertEquals(0, changes)
        }

    @Test
    fun `prices come from the store as given`() =
        runTest {
            backend.priceOf = { "$1.99" }
            val store = store()
            store.start()
            assertEquals("$1.99", store.price(supporter))
        }
}
