using Laching.Core.Store;
using Microsoft.UI.Dispatching;
using Windows.Services.Store;
using CoreProduct = Laching.Core.Store.StoreProduct;

namespace Laching.Windows.Shell;

/// <summary>
/// The Microsoft Store implementation of <see cref="IStoreBackend"/>, over <c>Windows.Services.Store</c>. Products are
/// matched by their Partner Center Product ID (<c>InAppOfferToken</c>), so store ids never appear in an app. Needs
/// package identity and the add-ons in Partner Center; without them loading fails and nothing is owned. Needs no
/// manifest capability: the Store broker does the network, not the app. Shows no UI except the Store's purchase
/// dialog, which it parents to <c>windowHandle()</c>.
/// <para>
/// Consumables (tips) are reported fulfilled right after they're bought, so they can be bought again. Windows has
/// no Ask to Buy, so a purchase is never <see cref="PurchaseOutcome.Pending"/>, and no restore call: licenses sync
/// on their own, so <see cref="RestoreAsync"/> only re-reads them. License changes from outside the app (a refund, a
/// purchase on another PC) arrive as a <see cref="TransactionUpdate"/> with an empty id.
/// </para>
/// </summary>
public sealed class StoreContextBackend : IStoreBackend, IDisposable
{
    private const string Durable = "Durable";
    private static readonly string[] Kinds = [Durable, "Consumable", "UnmanagedConsumable"];

    private readonly DispatcherQueue dispatcher;
    private readonly Func<nint> windowHandle;
    private readonly Action<string> log;
    private readonly Dictionary<string, (string StoreId, string Kind)> products = [];
    private StoreContext? context;
    private Action<TransactionUpdate>? onUpdate;

    /// <param name="dispatcher">The UI thread's queue, where updates are reported.</param>
    /// <param name="windowHandle">The HWND the purchase dialog belongs to (the window with the buy button).</param>
    /// <param name="log">Diagnostics (ids, counts and errors only), or nothing.</param>
    public StoreContextBackend(DispatcherQueue dispatcher, Func<nint> windowHandle, Action<string>? log = null)
    {
        this.dispatcher = dispatcher;
        this.windowHandle = windowHandle;
        this.log = log ?? (_ => { });
    }

    private StoreContext Context => context ??= StoreContext.GetDefault();

    public async Task<IReadOnlyList<CoreProduct>> LoadProductsAsync(IReadOnlyList<string> ids)
    {
        var result = await Context.GetAssociatedStoreProductsAsync(Kinds);
        if (result.ExtendedError is { } error)
            throw error;
        var listed = new List<CoreProduct>();
        foreach (var pair in result.Products)
        {
            var product = pair.Value;
            var token = product.InAppOfferToken;
            if (string.IsNullOrEmpty(token) || !ids.Contains(token))
                continue;
            products[token] = (product.StoreId, product.ProductKind);
            listed.Add(new CoreProduct(token, product.Price.FormattedPrice));
        }
        return listed;
    }

    public async Task<IReadOnlySet<string>> EntitledProductIdsAsync()
    {
        var owned = new HashSet<string>();
        try
        {
            var license = await Context.GetAppLicenseAsync();
            foreach (var pair in license.AddOnLicenses)
                if (pair.Value.IsActive && !string.IsNullOrEmpty(pair.Value.InAppOfferToken))
                    owned.Add(pair.Value.InAppOfferToken);
        }
        catch (Exception e)
        {
            log($"Reading the license failed: {e.GetType().Name} {e.Message}");
        }
        return owned;
    }

    public async Task<PurchaseOutcome> PurchaseAsync(string id)
    {
        if (!products.ContainsKey(id))
            await LoadProductsAsync(new[] { id });
        if (!products.TryGetValue(id, out var product))
            throw new InvalidOperationException($"The Store doesn't list {id}.");

        // The purchase dialog needs an owner window in a desktop app; without one the call fails.
        WinRT.Interop.InitializeWithWindow.Initialize(Context, windowHandle());
        var result = await Context.RequestPurchaseAsync(product.StoreId);
        switch (result.Status)
        {
            case StorePurchaseStatus.Succeeded:
            case StorePurchaseStatus.AlreadyPurchased:
                if (product.Kind != Durable)
                    await FulfillAsync(id, product.StoreId);
                return PurchaseOutcome.Purchased;
            case StorePurchaseStatus.NotPurchased:
                return PurchaseOutcome.Cancelled;
            default:
                throw result.ExtendedError ?? new InvalidOperationException($"Purchase failed: {result.Status}");
        }
    }

    /// <summary>Licenses sync on their own on Windows; the store re-reads them after this.</summary>
    public Task RestoreAsync() => Task.CompletedTask;

    public void ObserveTransactions(Action<TransactionUpdate> onUpdate)
    {
        if (this.onUpdate is null)
            Context.OfflineLicensesChanged += OnLicensesChanged;
        this.onUpdate = onUpdate;
    }

    public void Dispose()
    {
        if (onUpdate is not null && context is not null)
            context.OfflineLicensesChanged -= OnLicensesChanged;
        onUpdate = null;
    }

    // Arrives on a background thread.
    private void OnLicensesChanged(StoreContext sender, object args) =>
        dispatcher.TryEnqueue(() => onUpdate?.Invoke(new TransactionUpdate("", IsRevoked: false)));

    /// <summary>A consumable can't be bought again until it's reported used. A failure here doesn't undo the purchase.</summary>
    private async Task FulfillAsync(string id, string storeId)
    {
        try
        {
            var result = await Context.ReportConsumableFulfillmentAsync(storeId, 1, Guid.NewGuid());
            if (result.Status != StoreConsumableStatus.Succeeded)
                log($"Fulfilling {id}: {result.Status} {result.ExtendedError?.Message}");
        }
        catch (Exception e)
        {
            log($"Fulfilling {id} failed: {e.GetType().Name} {e.Message}");
        }
    }
}
