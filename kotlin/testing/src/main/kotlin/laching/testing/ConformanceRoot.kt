package laching.testing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import kotlin.io.path.isDirectory
import kotlin.io.path.isRegularFile
import kotlin.io.path.name
import kotlin.io.path.readText

/**
 * Finds the repository's `conformance/` folder from the working directory (walking up), so the vector tests run from
 * Gradle, an IDE or the command line alike. An app that consumes Laching as a submodule can point the search at its own
 * checkout with [find]'s `start`.
 */
public object ConformanceRoot {
    /** The folder holding [markerFile]; throws when no parent of [start] has it. */
    public fun find(
        markerFile: String = "conformance/README.md",
        start: Path = Paths.get(System.getProperty("user.dir")).toAbsolutePath(),
    ): Path {
        var folder: Path? = start
        while (folder != null) {
            if (folder.resolve(markerFile).isRegularFile()) return folder.resolve(markerFile).parent
            folder = folder.parent
        }
        throw IllegalStateException("no $markerFile above $start: run the tests inside the Laching checkout")
    }

    /** The files of `conformance/<folder>` whose names end with [suffix], sorted by name. */
    public fun files(
        folder: String,
        suffix: String = ".json",
    ): List<Path> {
        val directory = find().resolve(folder)
        check(directory.isDirectory()) { "no conformance folder $directory" }
        return Files.list(directory).use { stream ->
            stream.filter { it.isRegularFile() && it.name.endsWith(suffix) }.sorted(compareBy { it.name }).toList()
        }
    }

    /** A vector file parsed as JSON. */
    public fun load(file: Path): JsonElement = Json.parseToJsonElement(file.readText())
}
