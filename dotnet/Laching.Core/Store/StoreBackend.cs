namespace Laching.Core.Store;

/// <summary>A product the store listed.</summary>
/// <param name="Id">The catalog id.</param>
/// <param name="DisplayPrice">Localized by the store, e.g. "0,99 €".</param>
public sealed record StoreProduct(string Id, string DisplayPrice);

public enum PurchaseOutcome
{
    Purchased,

    /// <summary>Waiting for a parent's approval or a payment check.</summary>
    Pending,

    Cancelled,
}

/// <summary>
/// A transaction that arrived outside <see cref="IStoreBackend.PurchaseAsync"/>: an approval, a refund, or a
/// purchase on another device. <see cref="ProductId"/> is empty when the store only says that licenses changed
/// (Windows), which makes <see cref="PurchaseStore"/> re-read what the user owns.
/// </summary>
public sealed record TransactionUpdate(string ProductId, bool IsRevoked);

/// <summary>
/// What <see cref="PurchaseStore"/> needs from the store. <c>Laching.Windows</c>' <c>StoreContextBackend</c> in an
/// app, <see cref="MockStoreBackend"/> in tests and previews. The only part of a Laching app that reaches a store;
/// a backend never shows UI except the store's own purchase dialog. Calls come from one thread (the UI thread),
/// and a backend reports updates back on that thread.
/// </summary>
public interface IStoreBackend
{
    /// <summary>The listed products among <paramref name="ids"/>; throws when the store can't be reached.</summary>
    Task<IReadOnlyList<StoreProduct>> LoadProductsAsync(IReadOnlyList<string> ids);

    /// <summary>
    /// Unlockables the user owns, active and not refunded. Works offline. Throws when it can't read them, never
    /// answering an empty set for a failed read: <see cref="PurchaseStore"/> then keeps the last known set.
    /// </summary>
    Task<IReadOnlySet<string>> EntitledProductIdsAsync();

    /// <summary>Shows the store's purchase dialog; throws when the purchase fails.</summary>
    Task<PurchaseOutcome> PurchaseAsync(string id);

    /// <summary>Asks the store to re-sync purchases (Restore purchases).</summary>
    Task RestoreAsync();

    void ObserveTransactions(Action<TransactionUpdate> onUpdate);
}
