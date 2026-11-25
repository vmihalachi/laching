using Laching.Companion.Figures;
using Laching.Companion.Phrases;
using Laching.Companion.Settings;
using Laching.Companion.Skins;
using Laching.Testing.Conformance;

namespace Laching.Testing.Fixtures;

/// <summary>
/// The fixtures mechanism tests run over (Swift's <c>PhraseBank.fixture</c>, <c>CompanionDefaults.fixture</c>,
/// <c>FixtureArt</c>), loaded once from the vectors folder <see cref="ConformanceRoot.Find"/> finds, which must hold
/// Laching's <c>companion/</c> fixtures: found from this source file, so from Laching's own checkout (this repo, or an
/// app's submodule), else from the test binary's folder.
/// </summary>
public static class Fixture
{
    public static string Root { get; } = ConformanceRoot.Find();

    public static PhraseBank Bank { get; } = FixtureBank.Load(Root);

    public static CompanionDefaults Defaults => FixtureDefaults.Value;

    public static CharacterArt Vector { get; } = FixtureArt.Load(Root, "fixture-vector");

    public static CharacterArt Raster { get; } = FixtureArt.Load(Root, "fixture-raster");

    public static SkinCatalog Catalog { get; } = FixtureArt.Catalog(Root);
}
