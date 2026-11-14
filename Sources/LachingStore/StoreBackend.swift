#if os(macOS) || os(iOS)
    import Foundation

    /// A product the App Store listed.
    public struct StoreProduct: Identifiable, Equatable, Sendable {
        public let id: String
        /// Localized by the App Store, e.g. "0,99 €".
        public let displayPrice: String

        public init(id: String, displayPrice: String) {
            self.id = id
            self.displayPrice = displayPrice
        }
    }

    public enum PurchaseOutcome: Equatable, Sendable {
        case purchased
        /// Waiting for Ask to Buy or a payment check.
        case pending
        case cancelled
    }

    /// A transaction that arrived outside `purchase`: Ask to Buy approval,
    /// a refund, or a purchase on another device.
    public struct TransactionUpdate: Equatable, Sendable {
        public let productID: String
        public let isRevoked: Bool

        public init(productID: String, isRevoked: Bool) {
            self.productID = productID
            self.isRevoked = isRevoked
        }
    }

    /// What `PurchaseStore` needs from the App Store. `StoreKitBackend` in the app, `MockStoreBackend`
    /// (`LachingTesting`) in tests. The only part of a Laching app that talks to the network.
    @MainActor
    public protocol StoreBackend: AnyObject {
        func loadProducts(_ ids: [String]) async throws -> [StoreProduct]
        /// Verified, unrevoked non-consumables the user owns. Works offline. Never fails: StoreKit answers from the
        /// device's own transaction record, so an empty set means nothing is owned and `PurchaseStore` applies it. On
        /// the other ports a store can fail a read: the Windows backend then throws, and its `PurchaseStore` keeps the
        /// last known set, while Play's backend answers its cache (the last read that worked plus purchases since), or
        /// throws when that cache is empty.
        func entitledProductIDs() async -> Set<String>
        func purchase(_ id: String) async throws -> PurchaseOutcome
        /// Asks the App Store to re-sync transactions (Restore purchases).
        func restore() async throws
        func observeTransactions(_ onUpdate: @escaping @MainActor (TransactionUpdate) -> Void)
    }
#endif
