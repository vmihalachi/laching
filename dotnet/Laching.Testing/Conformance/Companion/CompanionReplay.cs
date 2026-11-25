using System.Runtime.CompilerServices;
using Laching.Companion.Figures;
using Laching.Companion.Phrases;
using Laching.Companion.Settings;

namespace Laching.Testing.Conformance;

/// <summary>
/// One call per vector kind of a companion app's vectors folder (<c>conformance/</c>, or <c>conformance/companion</c>
/// here), each opening the file and returning its problems (empty when the port agrees), so an app's replay test is
/// theories over <see cref="Files"/> and one assertion each. The bodies are the <c>*Replay.Verify</c> functions.
/// </summary>
public static class CompanionReplay
{
    /// <summary><c>phrases.json</c> in <paramref name="root"/>: the bank and its flavors in display order.</summary>
    public static IReadOnlyList<string> Phrases(string root, PhraseBank bank, IReadOnlyList<FlavorId> flavors) =>
        Open(Path.Combine(root, "phrases.json"), (s, f) => PhrasesReplay.Verify(s, f, bank, flavors));

    /// <summary><c>settings.json</c> in <paramref name="root"/>: the generic keys and defaults.</summary>
    public static IReadOnlyList<string> Settings(string root, CompanionDefaults defaults) =>
        Open(Path.Combine(root, "settings.json"), (s, f) => SettingsReplay.Verify(s, f, defaults));

    /// <summary><c>desktop.json</c> in <paramref name="root"/>: the desktop presence, the bubble times, the part delays.</summary>
    public static IReadOnlyList<string> Desktop(string root) => Open(Path.Combine(root, "desktop.json"), DesktopReplay.Verify);

    /// <summary>A <c>picker/*.json</c>.</summary>
    public static IReadOnlyList<string> Picker(string path, PhraseBank bank) => Open(path, (s, f) => PickerReplay.Verify(s, f, bank));

    /// <summary>A <c>quiet/*.json</c>: a whole timeline through the coordinator.</summary>
    public static IReadOnlyList<string> Quiet(string path, PhraseBank bank, CompanionDefaults defaults) =>
        Open(path, (s, f) => QuietReplay.Verify(s, f, bank, defaults));

    /// <summary>A <c>cadence/*.json</c>.</summary>
    public static IReadOnlyList<string> Cadence(string path) => Open(path, CadenceReplay.Verify);

    /// <summary>A <c>characters/*.json</c>: the art or the poses of one of <paramref name="characters"/>.</summary>
    public static IReadOnlyList<string> Character(string path, IReadOnlyList<RiggedCharacter> characters) =>
        Open(path, (s, f) => CharacterReplay.Verify(s, f, characters));

    /// <summary>
    /// The <c>.json</c> files in <paramref name="folder"/> of the vectors folder, one per row (xUnit's <c>MemberData</c>):
    /// <see cref="ConformanceRoot.Files"/> from the caller's source file (<paramref name="sourceFile"/>, left to the
    /// compiler), so an app's replay test finds the app's vectors, not Laching's beside this file in a submodule.
    /// </summary>
    public static IEnumerable<object[]> Files(
        string folder,
        string markerFile = "conformance/README.md",
        [CallerFilePath] string sourceFile = ""
    ) => ConformanceRoot.Files(folder, "*.json", markerFile, sourceFile);

    private static IReadOnlyList<string> Open(string path, Func<Stream, string, IReadOnlyList<string>> verify)
    {
        using var stream = File.OpenRead(path);
        return verify(stream, Path.GetFileName(path));
    }
}
