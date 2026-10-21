package laching.android

import android.app.Activity
import android.content.Context
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.ConsumeParams
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import laching.core.store.ProductCatalog
import laching.core.store.PurchaseOutcome
import laching.core.store.StoreBackend
import laching.core.store.StoreProduct
import laching.core.store.TransactionUpdate

/**
 * [StoreBackend] over Google Play Billing: connects on first use (and reconnects by itself), lists the catalog's
 * one-time products, runs the purchase flow from the app's current [activity], acknowledges unlockables and consumes
 * tips, and keeps what the user owns in SharedPreferences so [entitledProductIds] answers offline. Purchases that
 * finish outside [purchase] (a pending approval, a refund, another device) reach [observeTransactions]. The only
 * part of a Laching Android app that talks to Play; shows nothing but Play's own sheet.
 */
public class PlayBillingBackend(
    context: Context,
    private val catalog: ProductCatalog,
    private val activity: () -> Activity?,
    private val log: (String) -> Unit = {},
) : StoreBackend {
    /** Play refused or failed; [code] is a `BillingClient.BillingResponseCode`. */
    public class BillingException(
        public val code: Int,
        message: String,
    ) : Exception("$message (code $code)")

    private val preferences = context.getSharedPreferences("laching.billing", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val details = HashMap<String, ProductDetails>()
    private var onUpdate: ((TransactionUpdate) -> Unit)? = null
    private var inFlight: Pair<String, CompletableDeferred<PurchaseOutcome>>? = null
    private var connecting: CompletableDeferred<Unit>? = null

    private val client: BillingClient =
        BillingClient
            .newBuilder(context)
            .setListener { result, purchases -> onPurchasesUpdated(result, purchases) }
            .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
            .enableAutoServiceReconnection()
            .build()

    override suspend fun loadProducts(ids: List<String>): List<StoreProduct> {
        connect()
        val products =
            ids.map { id ->
                QueryProductDetailsParams.Product
                    .newBuilder()
                    .setProductId(id)
                    .setProductType(BillingClient.ProductType.INAPP)
                    .build()
            }
        val params = QueryProductDetailsParams.newBuilder().setProductList(products).build()
        // Play can answer one query twice (that the service disconnected, then that it timed out): a deferred keeps
        // the first answer, where resuming a continuation again would crash the app.
        val answer = CompletableDeferred<List<ProductDetails>>()
        client.queryProductDetailsAsync(params) { result, queryResult ->
            if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                answer.complete(queryResult.productDetailsList)
            } else {
                answer.completeExceptionally(result.toException("Loading products failed"))
            }
        }
        val found = answer.await()
        for (product in found) details[product.productId] = product
        return found.mapNotNull { product ->
            val price = product.oneTimePurchaseOfferDetails?.formattedPrice ?: return@mapNotNull null
            StoreProduct(product.productId, price)
        }
    }

    override suspend fun entitledProductIds(): Set<String> {
        val purchases =
            try {
                connect()
                queryPurchases()
            } catch (e: BillingException) {
                log("Reading purchases failed, using the cache: ${e.message}")
                return cachedOwned()
            }
        return syncOwned(purchases)
    }

    override suspend fun purchase(id: String): PurchaseOutcome {
        connect()
        val product =
            details[id] ?: run {
                loadProducts(listOf(id))
                details[id]
            } ?: throw BillingException(
                BillingClient.BillingResponseCode.ITEM_UNAVAILABLE,
                "Play doesn't list $id",
            )
        val activity =
            activity() ?: throw BillingException(BillingClient.BillingResponseCode.ERROR, "No activity to buy from")
        if (inFlight != null) {
            throw BillingException(BillingClient.BillingResponseCode.ERROR, "A purchase is already under way")
        }
        val params =
            BillingFlowParams
                .newBuilder()
                .setProductDetailsParamsList(
                    listOf(
                        BillingFlowParams.ProductDetailsParams
                            .newBuilder()
                            .setProductDetails(product)
                            .build(),
                    ),
                ).build()
        val outcome = CompletableDeferred<PurchaseOutcome>()
        inFlight = id to outcome
        try {
            val launched = client.launchBillingFlow(activity, params)
            return when (launched.responseCode) {
                BillingClient.BillingResponseCode.OK -> outcome.await()
                BillingClient.BillingResponseCode.USER_CANCELED -> PurchaseOutcome.CANCELLED
                BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED -> alreadyOwned(id)
                else -> throw launched.toException("Starting the purchase failed")
            }
        } finally {
            inFlight = null
        }
    }

    override suspend fun restore() {
        connect()
        syncOwned(queryPurchases())
    }

    override fun observeTransactions(onUpdate: (TransactionUpdate) -> Unit) {
        this.onUpdate = onUpdate
    }

    private suspend fun connect() {
        if (client.isReady) return
        connecting?.let { return it.await() }
        val ready = CompletableDeferred<Unit>()
        connecting = ready
        client.startConnection(
            object : BillingClientStateListener {
                override fun onBillingSetupFinished(result: BillingResult) {
                    if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                        ready.complete(Unit)
                    } else {
                        ready.completeExceptionally(result.toException("Connecting to Play failed"))
                    }
                    if (connecting === ready) connecting = null
                }

                override fun onBillingServiceDisconnected() {
                    // enableAutoServiceReconnection reconnects before the next call.
                }
            },
        )
        ready.await()
    }

    private suspend fun queryPurchases(): List<Purchase> {
        val params = QueryPurchasesParams.newBuilder().setProductType(BillingClient.ProductType.INAPP).build()
        val answer = CompletableDeferred<List<Purchase>>()
        client.queryPurchasesAsync(params) { result, purchases ->
            if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                answer.complete(purchases)
            } else {
                answer.completeExceptionally(result.toException("Reading purchases failed"))
            }
        }
        return answer.await()
    }

    /** Settles every bought purchase and caches the unlockables they hold as what the user owns, replacing the cache. */
    private fun syncOwned(purchases: List<Purchase>): Set<String> {
        val bought = purchases.filter { it.purchaseState == Purchase.PurchaseState.PURCHASED }
        bought.forEach(::settle)
        val owned = bought.flatMap(::unlockables).toSet()
        preferences.edit().putStringSet(OWNED_KEY, owned).apply()
        return owned
    }

    private fun unlockables(purchase: Purchase): List<String> = purchase.products.filter(catalog::isUnlockable)

    /** Acknowledges a bought unlockable, consumes a tip, so Play doesn't refund it after three days. Bought ones only. */
    private fun settle(purchase: Purchase) {
        val token = purchase.purchaseToken
        if (purchase.products.any(catalog::isTip)) {
            val params = ConsumeParams.newBuilder().setPurchaseToken(token).build()
            client.consumeAsync(params) { result, _ ->
                if (result.responseCode !=
                    BillingClient.BillingResponseCode.OK
                ) {
                    log("Consuming a tip failed: ${result.debugMessage}")
                }
            }
        } else if (!purchase.isAcknowledged) {
            val params = AcknowledgePurchaseParams.newBuilder().setPurchaseToken(token).build()
            client.acknowledgePurchase(params) { result ->
                if (result.responseCode !=
                    BillingClient.BillingResponseCode.OK
                ) {
                    log("Acknowledging failed: ${result.debugMessage}")
                }
            }
        }
    }

    private fun onPurchasesUpdated(
        result: BillingResult,
        purchases: List<Purchase>?,
    ) {
        scope.launch {
            val waitingFor = inFlight?.first
            val waiting = inFlight?.second
            when (result.responseCode) {
                BillingClient.BillingResponseCode.OK -> {
                    for (purchase in purchases.orEmpty()) {
                        val state = purchase.purchaseState
                        if (state == Purchase.PurchaseState.PURCHASED) {
                            settle(purchase)
                            preferences.edit().putStringSet(OWNED_KEY, cachedOwned() + unlockables(purchase)).apply()
                        }
                        for (id in purchase.products) {
                            if (waiting != null && id == waitingFor) {
                                val outcome =
                                    if (state ==
                                        Purchase.PurchaseState.PENDING
                                    ) {
                                        PurchaseOutcome.PENDING
                                    } else {
                                        PurchaseOutcome.PURCHASED
                                    }
                                waiting.complete(outcome)
                            } else if (state == Purchase.PurchaseState.PURCHASED) {
                                onUpdate?.invoke(TransactionUpdate(id, isRevoked = false))
                            }
                        }
                    }
                    // Play told us purchases changed but listed none we waited for: let the store re-read.
                    if (waiting != null && !waiting.isCompleted) waiting.complete(PurchaseOutcome.CANCELLED)
                    if (purchases.isNullOrEmpty()) onUpdate?.invoke(TransactionUpdate("", isRevoked = false))
                }

                BillingClient.BillingResponseCode.USER_CANCELED -> {
                    waiting?.complete(PurchaseOutcome.CANCELLED)
                }

                BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED -> {
                    val outcome = alreadyOwned(waitingFor)
                    waiting?.complete(outcome)
                }

                else -> {
                    waiting?.completeExceptionally(result.toException("The purchase failed"))
                }
            }
        }
    }

    /**
     * Play says [id] is owned already: an unlockable bought on another device (or not yet acknowledged) counts as
     * bought; a tip Play hasn't consumed yet is consumed now and thanks no one, so the next tap tips again.
     */
    private suspend fun alreadyOwned(id: String?): PurchaseOutcome {
        try {
            syncOwned(queryPurchases())
        } catch (e: BillingException) {
            log("Reading purchases failed: ${e.message}")
        }
        return if (id != null && catalog.isTip(id)) PurchaseOutcome.CANCELLED else PurchaseOutcome.PURCHASED
    }

    private fun cachedOwned(): Set<String> = preferences.getStringSet(OWNED_KEY, emptySet()).orEmpty().toSet()

    private fun BillingResult.toException(what: String): BillingException =
        BillingException(responseCode, "$what: ${debugMessage.ifEmpty { "no details" }}")

    private companion object {
        const val OWNED_KEY = "owned"
    }
}
