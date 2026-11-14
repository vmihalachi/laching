using Laching.Core.Settings;

namespace Laching.Core.Store;

/// <summary>Why a purchase or restore didn't go through. The app maps these to its own localized text.</summary>
public enum PurchaseError
{
    /// <summary>The purchase failed or was refused; the user hasn't been charged.</summary>
    PurchaseFailed,

    /// <summary>The store couldn't re-sync purchases.</summary>
    RestoreFailed,
}

/// <summary>Where the price lookup stands, so a buy button can say "getting price" or offer a retry.</summary>
public enum LoadState
{
    Idle,
    Loading,
    Loaded,

    /// <summary>The store didn't answer, or didn't list an unlockable.</summary>
    Failed,
}

/// <summary>
/// Ownership of the catalog's unlockables plus tips, over an <see cref="IStoreBackend"/>. The last known
/// <see cref="Owned"/> set is cached in the settings store under the cache key, so the UI doesn't flash locked at
/// launch before the store answers. Needs <see cref="StartAsync"/> once; it then follows transaction updates
/// (approvals, refunds, purchases on another device). Never shows UI of its own and never charges without
/// <see cref="BuyAsync"/> or <see cref="TipAsync"/>. Not thread-safe: drive it from one thread (the UI thread);
/// <see cref="Changed"/> is raised there after every change.
/// </summary>
public sealed class PurchaseStore
{
    private readonly IStoreBackend backend;
    private readonly ISettingsStore settings;
    private readonly string cacheKey;
    private readonly Action<string> log;
    private IReadOnlySet<string> owned;
    private bool started;

    /// <param name="catalog">The products to sell.</param>
    /// <param name="backend">The store.</param>
    /// <param name="settings">Where the last known ownership is cached (as strings).</param>
    /// <param name="cacheKey">The cache's key.</param>
    /// <param name="log">Diagnostics (ids and counts only), or nothing.</param>
    public PurchaseStore(
        ProductCatalog catalog,
        IStoreBackend backend,
        ISettingsStore settings,
        string cacheKey = "laching.ownedProducts",
        Action<string>? log = null
    )
    {
        Catalog = catalog;
        this.backend = backend;
        this.settings = settings;
        this.cacheKey = cacheKey;
        this.log = log ?? (_ => { });
        // Last known state, so the UI doesn't flash at launch.
        owned = settings.GetStrings(cacheKey)?.ToHashSet() ?? new HashSet<string>();
    }

    /// <summary>Raised after any state below changes.</summary>
    public event Action? Changed;

    public ProductCatalog Catalog { get; }

    /// <summary>The unlockables the user owns, active and not refunded. Tips never appear here.</summary>
    public IReadOnlySet<string> Owned
    {
        get => owned;
        private set
        {
            owned = value;
            settings.SetStrings(cacheKey, value.Order(StringComparer.Ordinal).ToArray());
        }
    }

    public IReadOnlyDictionary<string, StoreProduct> Products { get; private set; } =
        new Dictionary<string, StoreProduct>();

    /// <summary>The product being bought, to disable the buttons meanwhile.</summary>
    public string? Purchasing { get; private set; }

    public bool IsRestoring { get; private set; }

    /// <summary>A purchase is waiting for approval.</summary>
    public bool IsPending { get; private set; }

    /// <summary>Set after a tip goes through. The view shows its thank-you note, then calls <see cref="AcknowledgeTip"/>.</summary>
    public bool JustTipped { get; private set; }

    /// <summary>The last failure; cleared when the next purchase or restore starts, or by <see cref="ClearError"/>.</summary>
    public PurchaseError? Error { get; private set; }

    public LoadState LoadState { get; private set; } = LoadState.Idle;

    public bool IsBusy => Purchasing is not null || IsRestoring;

    public bool IsOwned(string id) => owned.Contains(id);

    /// <summary>The store's localized price, once loaded.</summary>
    public string? Price(string id) => Products.GetValueOrDefault(id)?.DisplayPrice;

    public async Task StartAsync()
    {
        if (started)
            return;
        started = true;
        backend.ObserveTransactions(update =>
        {
            if (!update.IsRevoked && Catalog.IsTip(update.ProductId))
            {
                IsPending = false;
                JustTipped = true;
                Changed?.Invoke();
            }
            _ = RefreshEntitlementsAsync();
        });
        await RefreshEntitlementsAsync();
        await LoadProductsAsync();
    }

    public async Task LoadProductsAsync()
    {
        if (LoadState == LoadState.Loading)
            return;
        LoadState = LoadState.Loading;
        Changed?.Invoke();
        var ids = Catalog.All;
        IReadOnlyList<StoreProduct> loaded;
        try
        {
            loaded = await backend.LoadProductsAsync(ids);
        }
        catch (Exception e)
        {
            log($"Loading products failed: {e.GetType().Name} {e.Message}");
            LoadState = LoadState.Failed;
            Changed?.Invoke();
            return;
        }
        var missing = ids.Except(loaded.Select(p => p.Id)).Order(StringComparer.Ordinal);
        log($"Loaded {loaded.Count}/{ids.Count} products; missing: {string.Join(", ", missing)}");
        var products = new Dictionary<string, StoreProduct>();
        foreach (var product in loaded)
            products.TryAdd(product.Id, product);
        Products = products;
        var complete = Catalog.Unlockables.Count == 0
            ? products.Count > 0
            : Catalog.Unlockables.All(products.ContainsKey);
        LoadState = complete ? LoadState.Loaded : LoadState.Failed;
        Changed?.Invoke();
    }

    /// <summary>Retries when a purchase screen appears, in case loading at launch came back empty.</summary>
    public async Task LoadProductsIfNeededAsync()
    {
        if (LoadState is LoadState.Loaded or LoadState.Loading)
            return;
        await LoadProductsAsync();
    }

    /// <summary>
    /// Rereads what the user owns. When the store can't say (offline, a failed license read), logs it and keeps the
    /// last known <see cref="Owned"/>, so a failed read never locks what was bought; never throws.
    /// </summary>
    public Task RefreshEntitlementsAsync() => TryRefreshEntitlementsAsync();

    /// <summary><see cref="RefreshEntitlementsAsync"/>, answering whether the store said what's owned.</summary>
    private async Task<bool> TryRefreshEntitlementsAsync()
    {
        IReadOnlySet<string> read;
        try
        {
            read = await backend.EntitledProductIdsAsync();
        }
        catch (Exception e)
        {
            log($"Reading what's owned failed, keeping the last known: {e.GetType().Name} {e.Message}");
            return false;
        }
        var entitled = read.Where(Catalog.IsUnlockable).ToHashSet();
        log($"Owned: {string.Join(", ", entitled.Order(StringComparer.Ordinal))}");
        var changed = false;
        if (entitled.Except(owned).Any() && IsPending)
        {
            IsPending = false;
            changed = true;
        }
        if (!entitled.SetEquals(owned))
        {
            Owned = entitled;
            changed = true;
        }
        if (changed)
            Changed?.Invoke();
        return true;
    }

    /// <summary>Buys an unlockable; <see cref="Owned"/> updates once the store confirms it.</summary>
    public Task BuyAsync(string id) => PurchaseAsync(id);

    /// <summary>Buys a tip: <see cref="JustTipped"/> is set and nothing is unlocked.</summary>
    public Task TipAsync(string id) => PurchaseAsync(id);

    public async Task RestoreAsync()
    {
        if (IsBusy)
            return;
        IsRestoring = true;
        Error = null;
        Changed?.Invoke();
        try
        {
            await backend.RestoreAsync();
        }
        catch (Exception e)
        {
            log($"Restore failed: {e.GetType().Name} {e.Message}");
            Error = PurchaseError.RestoreFailed;
        }
        finally
        {
            IsRestoring = false;
            Changed?.Invoke();
        }
        // A restore that can't read back what's owned didn't re-sync anything the user can see.
        if (!await TryRefreshEntitlementsAsync() && Error is null)
        {
            Error = PurchaseError.RestoreFailed;
            Changed?.Invoke();
        }
    }

    public void AcknowledgeTip()
    {
        if (!JustTipped)
            return;
        JustTipped = false;
        Changed?.Invoke();
    }

    public void ClearError()
    {
        if (Error is null)
            return;
        Error = null;
        Changed?.Invoke();
    }

    private async Task PurchaseAsync(string id)
    {
        if (IsBusy)
            return;
        Purchasing = id;
        Error = null;
        IsPending = false;
        Changed?.Invoke();
        try
        {
            switch (await backend.PurchaseAsync(id))
            {
                case PurchaseOutcome.Purchased:
                    if (Catalog.IsTip(id))
                        JustTipped = true;
                    await RefreshEntitlementsAsync();
                    break;
                case PurchaseOutcome.Pending:
                    IsPending = true;
                    break;
                case PurchaseOutcome.Cancelled:
                    break;
            }
        }
        catch (Exception e)
        {
            log($"Purchase of {id} failed: {e.GetType().Name} {e.Message}");
            Error = PurchaseError.PurchaseFailed;
        }
        finally
        {
            Purchasing = null;
            Changed?.Invoke();
        }
    }
}
