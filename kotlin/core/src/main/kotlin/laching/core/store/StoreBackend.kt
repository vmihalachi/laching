package laching.core.store

/**
 * A product the store listed.
 * @property id The catalog id.
 * @property displayPrice Localized by the store, e.g. "0,99 €".
 */
public data class StoreProduct(
    public val id: String,
    public val displayPrice: String,
)

public enum class PurchaseOutcome {
    PURCHASED,

    /** Waiting for a parent's approval or a payment check. */
    PENDING,
    CANCELLED,
}

/**
 * A transaction that arrived outside [StoreBackend.purchase]: an approval, a refund, or a purchase on another
 * device. [productId] is empty when the store only says that licenses changed, which makes [PurchaseStore] re-read
 * what the user owns.
 */
public data class TransactionUpdate(
    public val productId: String,
    public val isRevoked: Boolean,
)

/**
 * What [PurchaseStore] needs from the store. `laching.android`'s `PlayBillingBackend` in an app,
 * [MockStoreBackend] in tests and previews. The only part of a Laching app that reaches a store; a backend never
 * shows UI except the store's own purchase dialog. Calls come from one thread (the main thread), and a backend
 * reports updates back on that thread.
 */
public interface StoreBackend {
    /** The listed products among [ids]; throws when the store can't be reached. */
    public suspend fun loadProducts(ids: List<String>): List<StoreProduct>

    /**
     * Unlockables the user owns, active and not refunded. Works offline. A backend that can't read the store either
     * throws, and [PurchaseStore] keeps the last known set, or answers what it last knew, as `PlayBillingBackend`
     * does from its cache: the last read that worked plus purchases since. [PurchaseStore] applies whatever is
     * answered, so a backend whose cache is empty throws rather than answer it: an empty set means nothing is owned.
     */
    public suspend fun entitledProductIds(): Set<String>

    /** Shows the store's purchase dialog; throws when the purchase fails. */
    public suspend fun purchase(id: String): PurchaseOutcome

    /** Asks the store to re-sync purchases (Restore purchases). */
    public suspend fun restore()

    public fun observeTransactions(onUpdate: (TransactionUpdate) -> Unit)
}
