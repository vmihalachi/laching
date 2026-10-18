import Foundation
import LachingCompanion
import LachingCore

/// `settings.json`: every generic preference's storage key and first-launch value, the keys "Reset to defaults"
/// clears, the names and tables ports need, and the package's timing constants. `base(defaults:)` returns the tree so
/// an app adds its own keys (for example a voice choice, its `skins` list) before writing it.
public enum SettingsVector {
    public static let file = "settings.json"

    /// The tree for `defaults`, read back through a fresh `CompanionSettings` the way an app's first launch sees it.
    @MainActor
    public static func base(defaults: CompanionDefaults) -> JSONValue {
        let s = CompanionSettings(store: InMemorySettingsStore(), defaults: defaults)
        func ints(_ values: [Int]) -> JSONValue { .array(values.map(JSONValue.int)) }
        func optional(_ value: String?) -> JSONValue { value.map(JSONValue.string) ?? .null }
        let values: JSONValue = .object([
            "channels": .strings(s.channels.names), "frequency": .string(s.frequency.rawValue),
            "activeStartMinute": .int(s.activeStartMinute), "activeEndMinute": .int(s.activeEndMinute),
            "activeWeekdays": ints(s.activeWeekdays.sorted()), "quietHours": ints(s.quietHours.sorted()),
            "hushInFullScreen": .bool(s.hushInFullScreen), "greetOnReturn": .bool(s.greetOnReturn),
            "commentOnPower": .bool(s.commentOnPower), "commentOnAppearance": .bool(s.commentOnAppearance),
            "voiceIdentifier": optional(s.voiceIdentifier), "speechRate": .double(s.speechRate),
            "speechVolume": .double(s.speechVolume), "phraseFlavors": .strings(s.phraseFlavors.map(\.rawValue).sorted()),
            "mutedPhraseIDs": .strings(s.mutedPhraseIDs.sorted()),
            "appearance": .string(s.appearance.rawValue), "skin": .string(s.skin.rawValue),
            "shushedUntil": optional(s.shushedUntil.map { ConformanceSupport.string($0, in: TimeZone(identifier: "UTC")!) }),
            "showOnDesktop": .bool(s.showOnDesktop), "desktopSize": .string(s.desktopSize.rawValue),
            "desktopPosition": s.desktopPosition.map { ints([$0.x, $0.y]) } ?? .null,
            "hasCompletedOnboarding": .bool(s.hasCompletedOnboarding),
        ])
        var frequencies: [String: JSONValue] = [:]
        for f in Frequency.allCases { frequencies[f.rawValue] = ints([Int(f.interval), Int(f.jitter)]) }
        var keys: [String: JSONValue] = [:]
        for key in SettingsKeys.all { keys[key] = .string(key) }
        var sizes: [String: JSONValue] = [:]
        for size in DesktopSize.allCases { sizes[size.rawValue] = .int(Int(size.height)) }
        return .object([
            "defaults": values,
            "keys": .object(keys),
            "keyOrder": .strings(SettingsKeys.all),
            "resettable": .strings(SettingsKeys.resettable),
            "frequencies": .object(frequencies),
            "channels": .strings(Channels.all.names),
            "appearances": .strings(AppearanceSetting.allCases.map(\.rawValue)),
            "shushes": .strings(ChatterCadence.Shush.allCases.map(\.rawValue)),
            "desktopSizes": .object(sizes),
            "idleThresholdSeconds": .int(Int(ChatterCadence.idleThreshold)),
            "busyGraceSeconds": .int(Int(ChatterCadence.busyGrace)),
            "welcomeBackCooldownSeconds": .int(Int(CompanionCoordinator.welcomeBackCooldown)),
            "recentCapacity": .int(CompanionCoordinator.recentCapacity),
            "recentWindow": .int(PhrasePicker.recentWindow),
            "partBeatSeconds": .double(PhraseTiming.partBeat),
            "reappearDelaySeconds": .int(Int(DesktopPresence.reappearDelay)),
            "hourHideSeconds": .int(Int(DesktopPresence.hourHide)),
        ])
    }

    public static func files(_ tree: JSONValue) throws -> [String: Data] {
        [file: try ConformanceSupport.encode(tree)]
    }

    /// Replays a committed file against the tree the app builds now.
    public static func verify(_ data: Data, file: String, expected tree: JSONValue) throws -> [String] {
        // Both sides through the decoder, so a whole double (1.0, written "1") compares equal to itself.
        let current = try ConformanceSupport.decode(JSONValue.self, from: try ConformanceSupport.encode(tree))
        return try ConformanceSupport.decode(JSONValue.self, from: data) == current ? [] : ["\(file): differs from the Swift defaults"]
    }
}
