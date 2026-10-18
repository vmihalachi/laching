#if os(macOS)
    import LachingCore
    import LachingStore
    import LachingTesting
    import Observation
    import XCTest

    @MainActor
    final class PurchaseStoreTests: XCTestCase {
        private let supporter = "test.supporter"
        private let smallTip = "test.tip.small"
        private let mediumTip = "test.tip.medium"
        private var catalog: ProductCatalog { ProductCatalog(unlockables: [supporter], tips: [smallTip, mediumTip]) }

        private func makeBackend() -> MockStoreBackend { MockStoreBackend(catalog: catalog) }

        private func makeStore(_ backend: MockStoreBackend, settings: SettingsStore? = nil) -> PurchaseStore {
            PurchaseStore(catalog: catalog, backend: backend, settings: settings ?? InMemorySettingsStore(), cacheKey: "test.owned")
        }

        /// Lets the store's `Task { refreshEntitlements() }` from an update run.
        private func settle() async {
            for _ in 0..<5 { await Task.yield() }
        }

        func testCatalogListsUnlockablesFirst() {
            XCTAssertEqual(catalog.all, [supporter, smallTip, mediumTip])
            XCTAssertTrue(catalog.isUnlockable(supporter))
            XCTAssertFalse(catalog.isTip(supporter))
            XCTAssertTrue(catalog.isTip(smallTip))
        }

        func testBuyingAnUnlockableMakesItOwned() async {
            let backend = makeBackend()
            let store = makeStore(backend)
            await store.start()
            XCTAssertFalse(store.isOwned(supporter))
            XCTAssertEqual(store.owned, [])
            XCTAssertEqual(store.price(supporter), "€\(supporter.count)")
            XCTAssertEqual(store.loadState, .loaded)

            await store.buy(supporter)
            XCTAssertTrue(store.isOwned(supporter))
            XCTAssertEqual(store.owned, [supporter])
            XCTAssertEqual(backend.purchased, [supporter])
            XCTAssertFalse(store.justTipped)
            XCTAssertNil(store.error)
        }

        func testAViewReadingOwnershipHearsThePurchase() async {
            let settings = InMemorySettingsStore()
            let store = makeStore(makeBackend(), settings: settings)
            await store.start()
            let changed = Changed()
            withObservationTracking {
                _ = store.isOwned(supporter)
            } onChange: {
                changed.fire()
            }
            await store.tip(smallTip)
            XCTAssertFalse(changed.fired, "a tip unlocks nothing")
            await store.buy(supporter)
            XCTAssertTrue(changed.fired)
            XCTAssertEqual(settings.strings(forKey: "test.owned"), [supporter], "the change still writes through to the cache")
        }

        func testOwnershipIsCachedForNextLaunch() async {
            let settings = InMemorySettingsStore()
            let backend = makeBackend()
            backend.owned = [supporter]
            let first = makeStore(backend, settings: settings)
            await first.start()
            XCTAssertTrue(first.isOwned(supporter))
            // Before StoreKit answers, the cached value keeps the unlock steady.
            XCTAssertTrue(makeStore(makeBackend(), settings: settings).isOwned(supporter))
            XCTAssertEqual(settings.strings(forKey: "test.owned"), [supporter])
            // Another key is another cache.
            let other = PurchaseStore(catalog: catalog, backend: makeBackend(), settings: settings, cacheKey: "other")
            XCTAssertFalse(other.isOwned(supporter))
        }

        func testUserDefaultsConvenienceCachesInTheSuite() async {
            let defaults = UserDefaults(suiteName: "laching.tests.\(UUID().uuidString)")!
            let backend = makeBackend()
            backend.owned = [supporter]
            await PurchaseStore(catalog: catalog, backend: backend, defaults: defaults, cacheKey: "test.owned").start()
            XCTAssertEqual(defaults.stringArray(forKey: "test.owned"), [supporter])
            let settings = UserDefaultsSettingsStore(defaults: defaults)
            XCTAssertTrue(makeStore(makeBackend(), settings: settings).isOwned(supporter))
        }

        func testRefundLocksAgain() async {
            let backend = makeBackend()
            backend.owned = [supporter]
            let store = makeStore(backend)
            await store.start()
            XCTAssertTrue(store.isOwned(supporter))

            backend.owned = []
            backend.send(TransactionUpdate(productID: supporter, isRevoked: true))
            await settle()
            XCTAssertFalse(store.isOwned(supporter))
            XCTAssertEqual(store.owned, [])
        }

        func testTipsUnlockNothing() async {
            let backend = makeBackend()
            let store = makeStore(backend)
            await store.start()
            await store.tip(mediumTip)
            XCTAssertEqual(backend.purchased, [mediumTip])
            XCTAssertEqual(store.owned, [])
            XCTAssertFalse(store.isOwned(mediumTip))
            XCTAssertTrue(store.justTipped)
            store.acknowledgeTip()
            XCTAssertFalse(store.justTipped)
        }

        func testRevokedTipDoesNotThank() async {
            let backend = makeBackend()
            let store = makeStore(backend)
            await store.start()
            backend.send(TransactionUpdate(productID: smallTip, isRevoked: true))
            XCTAssertFalse(store.justTipped)
            backend.send(TransactionUpdate(productID: smallTip, isRevoked: false))
            XCTAssertTrue(store.justTipped)
        }

        func testCancelledPendingAndFailedPurchases() async {
            let backend = makeBackend()
            let store = makeStore(backend)
            await store.start()

            backend.nextOutcome = .cancelled
            await store.buy(supporter)
            XCTAssertFalse(store.isOwned(supporter))
            XCTAssertNil(store.error)
            XCTAssertFalse(store.isPending)

            backend.nextOutcome = .pending
            await store.buy(supporter)
            XCTAssertTrue(store.isPending)
            XCTAssertFalse(store.isOwned(supporter))

            // Ask to Buy approved later.
            backend.owned = [supporter]
            backend.send(TransactionUpdate(productID: supporter, isRevoked: false))
            await settle()
            XCTAssertTrue(store.isOwned(supporter))
            XCTAssertFalse(store.isPending)

            backend.failPurchase = true
            await store.tip(smallTip)
            XCTAssertEqual(store.error, .purchaseFailed)
            XCTAssertFalse(store.justTipped)
            XCTAssertNil(store.purchasing)
            XCTAssertFalse(store.isBusy)
        }

        func testRestoreRefreshesAndReportsFailures() async {
            let backend = makeBackend()
            let store = makeStore(backend)
            await store.start()
            backend.owned = [supporter]
            await store.restore()
            XCTAssertEqual(backend.restores, 1)
            XCTAssertTrue(store.isOwned(supporter))
            XCTAssertNil(store.error)
            XCTAssertFalse(store.isRestoring)

            backend.failRestore = true
            await store.restore()
            XCTAssertEqual(store.error, .restoreFailed)
            XCTAssertFalse(store.isRestoring)
        }

        func testMissingProductsLeavePricesEmpty() async {
            let backend = makeBackend()
            backend.failProducts = true
            let store = makeStore(backend)
            await store.start()
            XCTAssertNil(store.price(supporter))
            XCTAssertEqual(store.loadState, .failed)
        }

        func testMissingUnlockableFailsTheLoadButAMissingTipDoesNot() async {
            let backend = makeBackend()
            backend.missingProducts = [supporter]
            let store = makeStore(backend)
            await store.start()
            XCTAssertEqual(store.loadState, .failed)
            XCTAssertNotNil(store.price(smallTip))

            let tipless = makeBackend()
            tipless.missingProducts = [smallTip]
            let other = makeStore(tipless)
            await other.start()
            XCTAssertEqual(other.loadState, .loaded)
            XCTAssertNil(other.price(smallTip))
        }

        func testCatalogWithoutUnlockablesLoadsWhenAnythingLoads() async {
            let tipsOnly = ProductCatalog(unlockables: [], tips: [smallTip])
            let backend = MockStoreBackend(catalog: tipsOnly)
            let store = PurchaseStore(catalog: tipsOnly, backend: backend, settings: InMemorySettingsStore(), cacheKey: "test.owned")
            await store.start()
            XCTAssertEqual(store.loadState, .loaded)

            let empty = MockStoreBackend(catalog: tipsOnly)
            empty.missingProducts = [smallTip]
            let failed = PurchaseStore(catalog: tipsOnly, backend: empty, settings: InMemorySettingsStore(), cacheKey: "test.owned")
            await failed.start()
            XCTAssertEqual(failed.loadState, .failed)
        }

        func testRetryRecoversFromFailedLoad() async {
            let backend = makeBackend()
            backend.failProducts = true
            let store = makeStore(backend)
            XCTAssertEqual(store.loadState, .idle)
            await store.start()
            XCTAssertEqual(store.loadState, .failed)

            backend.failProducts = false
            await store.loadProductsIfNeeded()
            XCTAssertEqual(store.loadState, .loaded)
            XCTAssertNotNil(store.price(supporter))

            // Loaded already: another call doesn't ask again.
            backend.failProducts = true
            await store.loadProductsIfNeeded()
            XCTAssertEqual(store.loadState, .loaded)
        }
    }

    /// Whether an observation's `onChange` ran (it runs on the main actor, where the store changes).
    private final class Changed: @unchecked Sendable {
        private(set) var fired = false
        func fire() { fired = true }
    }
#endif
