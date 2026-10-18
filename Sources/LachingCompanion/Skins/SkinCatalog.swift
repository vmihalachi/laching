import Foundation

/// One skin an app offers: its art, and the product that unlocks it (nil = free).
public struct SkinEntry: Equatable, Sendable {
    public let art: CharacterArt
    /// The store product id that unlocks this skin; nil when it's free.
    public let unlockedBy: String?

    public init(art: CharacterArt, unlockedBy: String? = nil) {
        self.art = art
        self.unlockedBy = unlockedBy
    }

    public var id: SkinID { art.skin }
}

/// The skins an app offers, the default first. Decides which skin to draw from the one the person picked and the
/// products they own, so a refund or an unknown id drops back to the default. Pure; never talks to the store.
public struct SkinCatalog: Equatable, Sendable {
    public let skins: [SkinEntry]

    /// `skins[0]` is the default and must be free; ids must be unique.
    public init(_ skins: [SkinEntry]) {
        precondition(!skins.isEmpty, "a skin catalog needs at least the default skin")
        precondition(skins[0].unlockedBy == nil, "the default skin \(skins[0].id.rawValue) must be free")
        precondition(Set(skins.map(\.id)).count == skins.count, "skin ids must be unique")
        self.skins = skins
    }

    public var defaultSkin: SkinID { skins[0].id }

    public subscript(id: SkinID) -> SkinEntry? {
        skins.first { $0.id == id }
    }

    /// The character for `id`, or the default's when the catalog doesn't have it.
    public func character(for id: SkinID) -> RiggedCharacter {
        RiggedCharacter(art: (self[id] ?? skins[0]).art)
    }

    /// Whether `id` is in the catalog and free or unlocked by one of `owned` (store product ids).
    public func isUnlocked(_ id: SkinID, owned: Set<String>) -> Bool {
        guard let entry = self[id] else { return false }
        return entry.unlockedBy.map(owned.contains) ?? true
    }

    /// `selected` when it's unlocked, else the default: what to draw for a stored choice.
    public func effectiveSkin(selected: SkinID, owned: Set<String>) -> SkinID {
        isUnlocked(selected, owned: owned) ? selected : defaultSkin
    }
}
