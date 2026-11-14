namespace Laching.Core.Store;

/// <summary>
/// An <see cref="IStoreBackend"/> for tests and previews: every product asked for is listed (priced by
/// <see cref="PriceOf"/>, "€" plus the id's length unless set), buying an unlockable makes it owned, buying a tip
/// doesn't, and switches make loading, reading what's owned, buying or restoring fail. <see cref="Send"/> plays a
/// transaction update as if it came from the store. Never reaches a store. (The Swift package has it in
/// <c>LachingTesting</c>; .NET ships it here, like <c>InMemorySettingsStore</c>, because apps use it in their Debug
/// screenshot and preview modes too.)
/// </summary>
public sealed class MockStoreBackend(ProductCatalog catalog) : IStoreBackend
{
    private Action<TransactionUpdate>? onUpdate;

    public ProductCatalog Catalog { get; } = catalog;

    /// <summary>What <see cref="EntitledProductIdsAsync"/> answers; set it to simulate a refund or a purchase elsewhere.</summary>
    public HashSet<string> Owned { get; set; } = [];

    public PurchaseOutcome NextOutcome { get; set; } = PurchaseOutcome.Purchased;

    public bool FailPurchase { get; set; }

    public bool FailProducts { get; set; }

    public bool FailRestore { get; set; }

    /// <summary>
    /// Makes <see cref="EntitledProductIdsAsync"/> throw, as a backend does when it can't read what the user owns (an
    /// offline or failed license read), instead of answering an empty set.
    /// </summary>
    public bool FailEntitlements { get; set; }

    /// <summary>Ids <see cref="LoadProductsAsync"/> leaves out, as if the store didn't list them.</summary>
    public HashSet<string> MissingProducts { get; set; } = [];

    /// <summary>The display price for an id.</summary>
    public Func<string, string> PriceOf { get; set; } = id => $"€{id.Length}";

    public List<string> Purchased { get; } = [];

    public int Restores { get; private set; }

    /// <summary>The thrown error.</summary>
    public sealed class Failure : Exception;

    public Task<IReadOnlyList<StoreProduct>> LoadProductsAsync(IReadOnlyList<string> ids)
    {
        if (FailProducts)
            return Task.FromException<IReadOnlyList<StoreProduct>>(new Failure());
        IReadOnlyList<StoreProduct> products = ids.Where(id => !MissingProducts.Contains(id))
            .Select(id => new StoreProduct(id, PriceOf(id)))
            .ToArray();
        return Task.FromResult(products);
    }

    public Task<IReadOnlySet<string>> EntitledProductIdsAsync() =>
        FailEntitlements
            ? Task.FromException<IReadOnlySet<string>>(new Failure())
            : Task.FromResult<IReadOnlySet<string>>(Owned.ToHashSet());

    public Task<PurchaseOutcome> PurchaseAsync(string id)
    {
        if (FailPurchase)
            return Task.FromException<PurchaseOutcome>(new Failure());
        Purchased.Add(id);
        if (NextOutcome == PurchaseOutcome.Purchased && Catalog.IsUnlockable(id))
            Owned.Add(id);
        return Task.FromResult(NextOutcome);
    }

    public Task RestoreAsync()
    {
        if (FailRestore)
            return Task.FromException(new Failure());
        Restores++;
        return Task.CompletedTask;
    }

    public void ObserveTransactions(Action<TransactionUpdate> onUpdate) => this.onUpdate = onUpdate;

    /// <summary>Simulates a transaction arriving from outside the app (refund, approval, another device).</summary>
    public void Send(TransactionUpdate update) => onUpdate?.Invoke(update);
}
