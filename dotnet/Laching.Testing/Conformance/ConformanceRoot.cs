using System.Runtime.CompilerServices;

namespace Laching.Testing.Conformance;

/// <summary>
/// A repo's vectors folder, found by walking up from the calling source file (as Swift's <c>ConformanceRoot</c> does
/// from <c>#filePath</c>), then from the test binary's folder. The source file finds it wherever the build output goes
/// (<c>dotnet test --artifacts-path</c> outside the repo); the binary finds it when the source path is not on this
/// machine (a deterministic build's <c>/_/</c>, a package built elsewhere).
/// </summary>
public static class ConformanceRoot
{
    /// <summary>
    /// The folder holding <paramref name="markerFile"/> (a path such as "conformance/README.md"), searched for from the
    /// folder of <paramref name="sourceFile"/> up to the root, then from the test binary's folder up to the root.
    /// <paramref name="sourceFile"/> is the caller's source file: leave it to the compiler. A marker file rather than a
    /// folder name, because on a case-insensitive file system a test project's own <c>Conformance/</c> folder would
    /// match. Throws <see cref="DirectoryNotFoundException"/>, naming the folders it searched from, when none has it.
    /// </summary>
    public static string Find(string markerFile = "conformance/README.md", [CallerFilePath] string sourceFile = "")
    {
        var starts = Starts(sourceFile);
        foreach (var start in starts)
        {
            for (var dir = new DirectoryInfo(start); dir is not null; dir = dir.Parent)
            {
                var marker = Path.Combine(dir.FullName, markerFile);
                if (File.Exists(marker))
                    return Path.GetDirectoryName(marker)!;
            }
        }
        var searched = string.Join(" or ", starts);
        throw new DirectoryNotFoundException(
            $"no ancestor of {searched} has {markerFile}: the calling source file's folder and the test binary's are searched"
        );
    }

    /// <summary>
    /// The files matching <paramref name="pattern"/> in <paramref name="folder"/> (relative to the vectors folder
    /// <see cref="Find"/> finds from <paramref name="sourceFile"/>), in ordinal order, one per row: the shape xUnit's
    /// <c>MemberData</c> takes. Throws <see cref="DirectoryNotFoundException"/> when the folder is missing and
    /// <see cref="FileNotFoundException"/> when nothing in it matches, so a replay never passes over no vectors.
    /// </summary>
    public static IEnumerable<object[]> Files(
        string folder,
        string pattern = "*.json",
        string markerFile = "conformance/README.md",
        [CallerFilePath] string sourceFile = ""
    )
    {
        var path = Path.Combine(Find(markerFile, sourceFile), folder);
        var files = Directory.GetFiles(path, pattern);
        if (files.Length == 0)
            throw new FileNotFoundException($"no {pattern} in {path}");
        return files.Order(StringComparer.Ordinal).Select(f => new object[] { f }).ToList();
    }

    // The calling source file's folder when it is a full path on this machine's terms, then the test binary's.
    private static string[] Starts(string sourceFile) =>
        Path.IsPathFullyQualified(sourceFile) ? [Path.GetDirectoryName(sourceFile)!, AppContext.BaseDirectory] : [AppContext.BaseDirectory];
}
