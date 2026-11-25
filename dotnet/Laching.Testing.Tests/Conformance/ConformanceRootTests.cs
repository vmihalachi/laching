using System.Runtime.CompilerServices;

namespace Laching.Testing.Tests.Conformance;

/// <summary>
/// The vectors folder is found by its marker file from the calling source file, then from the test binary, so it is
/// found wherever the build output goes; a missing folder or an empty one is an error, never a pass over nothing.
/// </summary>
public sealed class ConformanceRootTests : IDisposable
{
    private readonly DirectoryInfo scratch = Directory.CreateTempSubdirectory("laching-conformance-");

    public void Dispose() => scratch.Delete(recursive: true);

    private static string ThisFolder([CallerFilePath] string file = "") => Path.GetDirectoryName(file)!;

    // A source file two folders under a scratch repo whose conformance/ holds the README and an empty folder.
    private string ScratchSource()
    {
        Directory.CreateDirectory(Path.Combine(scratch.FullName, "conformance", "empty"));
        File.WriteAllText(Path.Combine(scratch.FullName, "conformance", "README.md"), "");
        return Path.Combine(scratch.FullName, "Tests", "Deep", "ReplayTests.cs");
    }

    [Fact]
    public void FindsTheVectorsFolderByItsMarker()
    {
        var root = ConformanceRoot.Find();
        Assert.Equal("conformance", Path.GetFileName(root));
        Assert.True(File.Exists(Path.Combine(root, "README.md")));
        Assert.True(Directory.Exists(Path.Combine(root, "companion")));
    }

    [Fact]
    public void FindsItFromTheCallingSourceFileWhereverTheBinaryIs() =>
        // dotnet/Laching.Testing.Tests/Conformance/ → the repo's conformance/, whether or not the binary is in the repo.
        Assert.Equal(Path.GetFullPath(Path.Combine(ThisFolder(), "..", "..", "..", "conformance")), ConformanceRoot.Find());

    [Fact]
    public void TheSourceFileComesBeforeTheBinary() =>
        Assert.Equal(Path.Combine(scratch.FullName, "conformance"), ConformanceRoot.Find(sourceFile: ScratchSource()));

    [Theory]
    [InlineData("")] // no source path
    [InlineData("/_/Tests/ReplayTests.cs")] // a deterministic build's mapped path
    [InlineData("Tests/ReplayTests.cs")] // a relative one
    public void FallsBackToTheTestBinarysFolder(string source)
    {
        // The binary's own dll as the marker: found from its folder when the source path is not on this machine.
        var binary = Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory);
        Assert.Equal(binary, ConformanceRoot.Find("Laching.Testing.Tests.dll", source));
        var elsewhere = Path.Combine(scratch.FullName, "not-here", "ReplayTests.cs");
        Assert.Equal(binary, ConformanceRoot.Find("Laching.Testing.Tests.dll", elsewhere));
    }

    [Fact]
    public void AMissingMarkerIsAnErrorNamingWhereItLooked()
    {
        var error = Assert.Throws<DirectoryNotFoundException>(() => ConformanceRoot.Find($"no/such/marker-{Guid.NewGuid()}"));
        Assert.Contains(ThisFolder(), error.Message);
        Assert.Contains(AppContext.BaseDirectory, error.Message);
    }

    [Fact]
    public void FilesListsAFolderInOrdinalOrderOnePerRow()
    {
        var files = ConformanceRoot.Files("companion").Select(row => Assert.IsType<string>(Assert.Single(row))).ToList();
        Assert.NotEmpty(files);
        Assert.Equal(files.Order(StringComparer.Ordinal), files);
        Assert.All(files, f => Assert.EndsWith(".json", f));
        Assert.Contains(files, f => Path.GetFileName(f) == "phrases.json");
    }

    [Fact]
    public void FilesOverAMissingOrEmptyFolderIsAnError()
    {
        var source = ScratchSource();
        Assert.Throws<DirectoryNotFoundException>(() => ConformanceRoot.Files("missing", sourceFile: source));
        var error = Assert.Throws<FileNotFoundException>(() => ConformanceRoot.Files("empty", sourceFile: source));
        Assert.Contains(Path.Combine(scratch.FullName, "conformance", "empty"), error.Message);
    }

    [Fact]
    public void CompanionReplayFilesSearchesFromItsCaller()
    {
        var source = ScratchSource();
        File.WriteAllText(Path.Combine(scratch.FullName, "conformance", "empty", "a.json"), "{}");
        var row = Assert.Single(CompanionReplay.Files("empty", sourceFile: source));
        Assert.Equal(Path.Combine(scratch.FullName, "conformance", "empty", "a.json"), Assert.Single(row));
    }
}
