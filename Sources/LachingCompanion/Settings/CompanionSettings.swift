import Foundation
import LachingCore
import Observation

/// Every generic companion preference, observable, written through `store` under `SettingsKeys` as soon as it
/// changes. First-launch values come from `defaults`; a missing or unreadable value falls back to the default.
/// Reading writes nothing. Never touches the network; where values live is the store's business. Isolated to the main
/// actor, and so is every subclass: read and write it there.
///
/// An app subclasses it to add its own preferences, under keys of its own, with the same write-through pattern:
///
///     @Observable
///     final class AppSettings: CompanionSettings {
///         var voice: String? {
///             didSet {
///                 if let voice { store.set(string: voice, forKey: "voice") } else { store.remove(forKey: "voice") }
///             }
///         }
///
///         override init(store: any SettingsStore, defaults: CompanionDefaults) {
///             voice = store.string(forKey: "voice")
///             super.init(store: store, defaults: defaults)
///         }
///
///         override func resetToDefaults() {
///             super.resetToDefaults()
///             voice = nil
///         }
///     }
///
/// Mark the subclass `@Observable` too, so its own properties notify (the base's always do), and pass it wherever a
/// `CompanionSettings` is wanted (`CompanionCoordinator`, `ChatterCadence.config(for:)`).
@MainActor
@Observable
open class CompanionSettings {
    public let store: any SettingsStore
    public let defaults: CompanionDefaults

    public var channels: Channels {
        didSet { store.set(strings: channels.names, forKey: SettingsKeys.channels) }
    }
    public var frequency: Frequency {
        didSet { store.set(string: frequency.rawValue, forKey: SettingsKeys.frequency) }
    }
    public var activeStartMinute: Int {
        didSet { store.set(integer: activeStartMinute, forKey: SettingsKeys.activeStartMinute) }
    }
    public var activeEndMinute: Int {
        didSet { store.set(integer: activeEndMinute, forKey: SettingsKeys.activeEndMinute) }
    }
    public var activeWeekdays: Set<Int> {
        didSet { store.set(integers: activeWeekdays.sorted(), forKey: SettingsKeys.activeWeekdays) }
    }
    public var quietHours: Set<Int> {
        didSet { store.set(integers: quietHours.sorted(), forKey: SettingsKeys.quietHours) }
    }
    public var hushInFullScreen: Bool {
        didSet { store.set(bool: hushInFullScreen, forKey: SettingsKeys.hushInFullScreen) }
    }
    public var greetOnReturn: Bool {
        didSet { store.set(bool: greetOnReturn, forKey: SettingsKeys.greetOnReturn) }
    }
    public var commentOnPower: Bool {
        didSet { store.set(bool: commentOnPower, forKey: SettingsKeys.commentOnPower) }
    }
    public var commentOnAppearance: Bool {
        didSet { store.set(bool: commentOnAppearance, forKey: SettingsKeys.commentOnAppearance) }
    }
    public var voiceIdentifier: String? {
        didSet {
            if let voiceIdentifier {
                store.set(string: voiceIdentifier, forKey: SettingsKeys.voiceIdentifier)
            } else {
                store.remove(forKey: SettingsKeys.voiceIdentifier)
            }
        }
    }
    public var speechRate: Double {
        didSet { store.set(double: speechRate, forKey: SettingsKeys.speechRate) }
    }
    public var speechVolume: Double {
        didSet { store.set(double: speechVolume, forKey: SettingsKeys.speechVolume) }
    }
    /// What the character talks about. Empty would mean every flavor; the app's Settings keeps at least one on.
    /// Ids are kept as stored, so the app drops any it retires.
    public var phraseFlavors: Set<FlavorID> {
        didSet { store.set(strings: phraseFlavors.map(\.rawValue).sorted(), forKey: SettingsKeys.phraseFlavors) }
    }
    /// Lines the user asked not to hear again (phrase ids; retired ids are harmless).
    public var mutedPhraseIDs: Set<String> {
        didSet { store.set(strings: mutedPhraseIDs.sorted(), forKey: SettingsKeys.mutedPhraseIDs) }
    }
    public var appearance: AppearanceSetting {
        didSet { store.set(string: appearance.rawValue, forKey: SettingsKeys.appearance) }
    }
    public var skin: SkinID {
        didSet { store.set(string: skin.rawValue, forKey: SettingsKeys.skin) }
    }
    public var shushedUntil: Date? {
        didSet {
            if let shushedUntil {
                store.set(double: shushedUntil.timeIntervalSince1970, forKey: SettingsKeys.shushedUntil)
            } else {
                store.remove(forKey: SettingsKeys.shushedUntil)
            }
        }
    }
    public var showOnDesktop: Bool {
        didSet { store.set(bool: showOnDesktop, forKey: SettingsKeys.showOnDesktop) }
    }
    public var desktopSize: DesktopSize {
        didSet { store.set(string: desktopSize.rawValue, forKey: SettingsKeys.desktopSize) }
    }
    /// Stored as `[x, y]`; removed when the character goes back to its corner.
    public var desktopPosition: DesktopPosition? {
        didSet {
            if let desktopPosition {
                store.set(integers: [desktopPosition.x, desktopPosition.y], forKey: SettingsKeys.desktopPosition)
            } else {
                store.remove(forKey: SettingsKeys.desktopPosition)
            }
        }
    }
    public var hasCompletedOnboarding: Bool {
        didSet { store.set(bool: hasCompletedOnboarding, forKey: SettingsKeys.hasCompletedOnboarding) }
    }

    public init(store: any SettingsStore, defaults: CompanionDefaults) {
        self.store = store
        self.defaults = defaults
        channels = store.strings(forKey: SettingsKeys.channels).map(Channels.init(names:)) ?? defaults.channels
        frequency = store.string(forKey: SettingsKeys.frequency).flatMap(Frequency.init(rawValue:)) ?? defaults.frequency
        activeStartMinute = store.integer(forKey: SettingsKeys.activeStartMinute) ?? defaults.activeStartMinute
        activeEndMinute = store.integer(forKey: SettingsKeys.activeEndMinute) ?? defaults.activeEndMinute
        activeWeekdays = store.integers(forKey: SettingsKeys.activeWeekdays).map(Set.init) ?? defaults.activeWeekdays
        quietHours = store.integers(forKey: SettingsKeys.quietHours).map(Set.init) ?? defaults.quietHours
        hushInFullScreen = store.bool(forKey: SettingsKeys.hushInFullScreen) ?? defaults.hushInFullScreen
        greetOnReturn = store.bool(forKey: SettingsKeys.greetOnReturn) ?? defaults.greetOnReturn
        commentOnPower = store.bool(forKey: SettingsKeys.commentOnPower) ?? defaults.commentOnPower
        commentOnAppearance = store.bool(forKey: SettingsKeys.commentOnAppearance) ?? defaults.commentOnAppearance
        voiceIdentifier = store.string(forKey: SettingsKeys.voiceIdentifier) ?? defaults.voiceIdentifier
        speechRate = store.double(forKey: SettingsKeys.speechRate) ?? defaults.speechRate
        speechVolume = store.double(forKey: SettingsKeys.speechVolume) ?? defaults.speechVolume
        phraseFlavors =
            store.strings(forKey: SettingsKeys.phraseFlavors).map { Set($0.map(FlavorID.init(rawValue:))) }
            ?? defaults.phraseFlavors
        mutedPhraseIDs = store.strings(forKey: SettingsKeys.mutedPhraseIDs).map(Set.init) ?? defaults.mutedPhraseIDs
        appearance =
            store.string(forKey: SettingsKeys.appearance).flatMap(AppearanceSetting.init(rawValue:)) ?? defaults.appearance
        skin = store.string(forKey: SettingsKeys.skin).flatMap { $0.isEmpty ? nil : SkinID(rawValue: $0) } ?? defaults.skin
        shushedUntil =
            store.double(forKey: SettingsKeys.shushedUntil).map(Date.init(timeIntervalSince1970:)) ?? defaults.shushedUntil
        showOnDesktop = store.bool(forKey: SettingsKeys.showOnDesktop) ?? defaults.showOnDesktop
        desktopSize =
            store.string(forKey: SettingsKeys.desktopSize).flatMap(DesktopSize.init(rawValue:)) ?? defaults.desktopSize
        desktopPosition =
            store.integers(forKey: SettingsKeys.desktopPosition).flatMap(Self.decodePosition) ?? defaults.desktopPosition
        hasCompletedOnboarding = store.bool(forKey: SettingsKeys.hasCompletedOnboarding) ?? defaults.hasCompletedOnboarding
    }

    /// The settings `QuietRules` consults.
    public var manners: Manners {
        Manners(
            channels: channels, greetOnReturn: greetOnReturn, commentOnPower: commentOnPower,
            commentOnAppearance: commentOnAppearance)
    }

    /// Puts every resettable preference back to its default (onboarding stays done) and removes their keys from the
    /// store, so a user who reset follows the app's defaults when a later version changes them. A subclass overrides
    /// it to reset its own preferences too, removing its own keys the same way, and calls `super`.
    open func resetToDefaults() {
        channels = defaults.channels
        frequency = defaults.frequency
        activeStartMinute = defaults.activeStartMinute
        activeEndMinute = defaults.activeEndMinute
        activeWeekdays = defaults.activeWeekdays
        quietHours = defaults.quietHours
        hushInFullScreen = defaults.hushInFullScreen
        greetOnReturn = defaults.greetOnReturn
        commentOnPower = defaults.commentOnPower
        commentOnAppearance = defaults.commentOnAppearance
        voiceIdentifier = defaults.voiceIdentifier
        speechRate = defaults.speechRate
        speechVolume = defaults.speechVolume
        phraseFlavors = defaults.phraseFlavors
        mutedPhraseIDs = defaults.mutedPhraseIDs
        appearance = defaults.appearance
        skin = defaults.skin
        shushedUntil = defaults.shushedUntil
        showOnDesktop = defaults.showOnDesktop
        desktopSize = defaults.desktopSize
        desktopPosition = defaults.desktopPosition
        for key in SettingsKeys.resettable {
            store.remove(forKey: key)
        }
    }

    /// `[x, y]`; anything else is unreadable and falls back to the default spot.
    static func decodePosition(_ values: [Int]) -> DesktopPosition? {
        values.count == 2 ? DesktopPosition(x: values[0], y: values[1]) : nil
    }
}
