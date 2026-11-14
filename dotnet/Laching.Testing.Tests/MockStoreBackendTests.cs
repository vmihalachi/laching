using Laching.Core.Store;

namespace Laching.Testing.Tests;

/// <summary><see cref="MockStoreBackend"/>'s purchase, restore and failure paths, and <c>Send</c> reaching the observer.</summary>
public sealed class MockStoreBackendTests
{
    private const string Supporter = "test.supporter";

    private const string Tip = "test.tip";

    private static MockStoreBackend MakeBackend() => new(new ProductCatalog([Supporter], [Tip]));

    [Fact]
    public async Task ListsEveryProductPricedByItsIdsLength()
    {
        var products = await MakeBackend().LoadProductsAsync([Supporter, Tip]);
        Assert.Equal([Supporter, Tip], products.Select(p => p.Id));
        Assert.Equal(["€14", "€8"], products.Select(p => p.DisplayPrice));
    }

    [Fact]
    public async Task PricesCanBeReplaced()
    {
        var backend = MakeBackend();
        backend.PriceOf = id => id == Tip ? "$1.99" : "$4.99";
        var products = await backend.LoadProductsAsync([Supporter, Tip]);
        Assert.Equal(["$4.99", "$1.99"], products.Select(p => p.DisplayPrice));
    }

    [Fact]
    public async Task MissingProductsAreLeftOut()
    {
        var backend = MakeBackend();
        backend.MissingProducts.Add(Tip);
        var products = await backend.LoadProductsAsync([Supporter, Tip]);
        Assert.Equal([Supporter], products.Select(p => p.Id));
    }

    [Fact]
    public async Task LoadingCanFail()
    {
        var backend = MakeBackend();
        backend.FailProducts = true;
        await Assert.ThrowsAsync<MockStoreBackend.Failure>(() => backend.LoadProductsAsync([Supporter]));
    }

    [Fact]
    public async Task BuyingAnUnlockableMakesItOwned()
    {
        var backend = MakeBackend();
        Assert.Equal(PurchaseOutcome.Purchased, await backend.PurchaseAsync(Supporter));
        Assert.Equal([Supporter], backend.Purchased);
        Assert.Equal(new HashSet<string> { Supporter }, await backend.EntitledProductIdsAsync());
    }

    [Fact]
    public async Task BuyingATipOwnsNothing()
    {
        var backend = MakeBackend();
        Assert.Equal(PurchaseOutcome.Purchased, await backend.PurchaseAsync(Tip));
        Assert.Equal([Tip], backend.Purchased);
        Assert.Empty(backend.Owned);
    }

    [Fact]
    public async Task APendingOrCancelledPurchaseOwnsNothing()
    {
        var backend = MakeBackend();
        backend.NextOutcome = PurchaseOutcome.Pending;
        var pending = await backend.PurchaseAsync(Supporter);
        backend.NextOutcome = PurchaseOutcome.Cancelled;
        var cancelled = await backend.PurchaseAsync(Supporter);
        Assert.Equal([PurchaseOutcome.Pending, PurchaseOutcome.Cancelled], [pending, cancelled]);
        Assert.Equal([Supporter, Supporter], backend.Purchased);
        Assert.Empty(backend.Owned);
    }

    [Fact]
    public async Task AFailedPurchaseIsNotRecorded()
    {
        var backend = MakeBackend();
        backend.FailPurchase = true;
        await Assert.ThrowsAsync<MockStoreBackend.Failure>(() => backend.PurchaseAsync(Supporter));
        Assert.Empty(backend.Purchased);
        Assert.Empty(backend.Owned);
    }

    [Fact]
    public async Task RestoresAreCountedUntilTheyFail()
    {
        var backend = MakeBackend();
        await backend.RestoreAsync();
        await backend.RestoreAsync();
        Assert.Equal(2, backend.Restores);
        backend.FailRestore = true;
        await Assert.ThrowsAsync<MockStoreBackend.Failure>(backend.RestoreAsync);
        Assert.Equal(2, backend.Restores);
    }

    /// <summary>A failed read throws rather than answering an empty set, and what's owned is still there once it reads again.</summary>
    [Fact]
    public async Task ReadingWhatIsOwnedCanFail()
    {
        var backend = MakeBackend();
        backend.Owned = [Supporter];
        backend.FailEntitlements = true;
        await Assert.ThrowsAsync<MockStoreBackend.Failure>(backend.EntitledProductIdsAsync);
        backend.FailEntitlements = false;
        Assert.Equal(new HashSet<string> { Supporter }, await backend.EntitledProductIdsAsync());
    }

    /// <summary>Like Swift's value-typed set: what a caller got stays as it was when a later purchase changes the backend.</summary>
    [Fact]
    public async Task EntitledIdsAreACopy()
    {
        var backend = MakeBackend();
        var before = await backend.EntitledProductIdsAsync();
        await backend.PurchaseAsync(Supporter);
        Assert.Empty(before);
        Assert.Contains(Supporter, await backend.EntitledProductIdsAsync());
    }

    [Fact]
    public void SendReachesTheObserverOnceThereIsOne()
    {
        var backend = MakeBackend();
        var refund = new TransactionUpdate(Supporter, true);
        var received = new List<TransactionUpdate>();
        backend.Send(refund);
        backend.ObserveTransactions(received.Add);
        backend.Send(refund);
        Assert.Equal([refund], received);
    }
}
