package laching.core.store

/**
 * A [StoreBackend] for tests and previews: every product asked for is listed (priced by [priceOf], "€" plus the
 * id's length unless set), buying an unlockable makes it owned, buying a tip doesn't, and switches make loading,
 * reading what's owned, buying or restoring fail. [send] plays a transaction update as if it came from the store.
 * Never reaches a store.
 * (Kotlin ships it in `:core`, as .NET does, because apps use it in their Debug screenshot and preview modes too;
 * `laching.testing` re-exports it.)
 */
public class MockStoreBackend(
    public val catalog: ProductCatalog,
) : StoreBackend {
    private var onUpdate: ((TransactionUpdate) -> Unit)? = null

    /** What [entitledProductIds] answers; set it to simulate a refund or a purchase elsewhere. */
    public var owned: MutableSet<String> = mutableSetOf()
    public var nextOutcome: PurchaseOutcome = PurchaseOutcome.PURCHASED
    public var failPurchase: Boolean = false
    public var failProducts: Boolean = false
    public var failRestore: Boolean = false

    /**
     * Makes [entitledProductIds] throw, as a backend does when it can't read what the user owns (an offline or failed
     * purchases read), instead of answering an empty set.
     */
    public var failEntitlements: Boolean = false

    /** Ids [loadProducts] leaves out, as if the store didn't list them. */
    public var missingProducts: MutableSet<String> = mutableSetOf()

    /** The display price for an id. */
    public var priceOf: (String) -> String = { id -> "€${id.length}" }
    public val purchased: MutableList<String> = mutableListOf()
    public var restores: Int = 0
        private set

    /** The thrown error. */
    public class Failure : Exception("The mock store failed, as asked")

    override suspend fun loadProducts(ids: List<String>): List<StoreProduct> {
        if (failProducts) throw Failure()
        return ids.filter { it !in missingProducts }.map { StoreProduct(it, priceOf(it)) }
    }

    override suspend fun entitledProductIds(): Set<String> {
        if (failEntitlements) throw Failure()
        return owned.toSet()
    }

    override suspend fun purchase(id: String): PurchaseOutcome {
        if (failPurchase) throw Failure()
        purchased += id
        if (nextOutcome == PurchaseOutcome.PURCHASED && catalog.isUnlockable(id)) owned += id
        return nextOutcome
    }

    override suspend fun restore() {
        if (failRestore) throw Failure()
        restores++
    }

    override fun observeTransactions(onUpdate: (TransactionUpdate) -> Unit) {
        this.onUpdate = onUpdate
    }

    /** Simulates a transaction arriving from outside the app (refund, approval, another device). */
    public fun send(update: TransactionUpdate) {
        onUpdate?.invoke(update)
    }
}
