import Foundation
import LachingCompanion

/// A companion app's pinned content, for `ConformanceSuite.companion`: the bank and its flavors, the defaults, the
/// characters, `settings.json`'s tree and the case lists (usually `PickerVector.standardCases` and
/// `QuietTimeline.standardCases` plus the app's own). Main-actor isolated, so a conforming type is too and builds
/// `settings` through `SettingsVector.base(defaults:)`.
@MainActor
public protocol CompanionVectors {
    static var bank: PhraseBank { get }
    /// The flavors in display order.
    static var flavors: [FlavorID] { get }
    static var defaults: CompanionDefaults { get }
    static var characters: [RiggedCharacter] { get }
    /// `settings.json`: `SettingsVector.base(defaults:)` plus the app's own keys.
    static var settings: JSONValue { get }
    static var pickerCases: [PickerVector.Case] { get }
    static var quietCases: [QuietTimeline.Case] { get }
}

extension ConformanceSuite {
    /// A companion app's whole suite over `vectors`: `phrases.json`, `settings.json`, `picker/`, `quiet/` and the
    /// characters' poses, generated and replayed. `picker/` and `quiet/` are the generator's own folders (a file no case
    /// writes is stale); `characters/` also holds the compiled art, so it isn't.
    @MainActor
    public static func companion<Vectors: CompanionVectors>(
        _ vectors: Vectors.Type, root: URL, regenerateHint: String
    ) -> ConformanceSuite {
        // Read once, so the closures hold values rather than the type.
        let (bank, flavors, defaults, characters) = (vectors.bank, vectors.flavors, vectors.defaults, vectors.characters)
        let (settings, pickerCases, quietCases) = (vectors.settings, vectors.pickerCases, vectors.quietCases)
        return ConformanceSuite(
            root: root,
            generatedFolders: [PickerVector.directory, QuietTimeline.directory],
            generate: {
                var files = try PhrasesVector.files(bank: bank, flavors: flavors)
                files.merge(try SettingsVector.files(settings)) { a, _ in a }
                files.merge(try PickerVector.files(cases: pickerCases, bank: bank)) { a, _ in a }
                files.merge(try QuietTimeline.files(cases: quietCases, bank: bank, defaults: defaults)) { a, _ in a }
                for character in characters { files.merge(try CharacterPoseVector.files(character)) { a, _ in a } }
                return files
            },
            replayers: [
                .init(path: PhrasesVector.file) { try PhrasesVector.verify($0, file: $1, bank: bank, flavors: flavors) },
                .init(path: SettingsVector.file) { try SettingsVector.verify($0, file: $1, expected: settings) },
                .init(path: PickerVector.directory) { try PickerVector.verify($0, file: $1, bank: bank) },
                .init(path: QuietTimeline.directory) { try QuietTimeline.verify($0, file: $1, bank: bank, defaults: defaults) },
                .init(path: CharacterPoseVector.directory) { try CharacterPoseVector.verify($0, file: $1, characters: characters) },
            ],
            regenerateHint: regenerateHint)
    }
}
