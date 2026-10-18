import LachingCompanion
import LachingTesting
import XCTest

/// Keeps `conformance/companion/` in step with the Swift reference, over the fixtures. The vectors are generated here
/// and replayed by `dotnet/Laching.Companion.Tests`; change one only by regenerating it (`scripts/conformance.sh`,
/// which runs this class's `testWriteVectors` too), never by hand.
@MainActor
final class ConformanceTests: XCTestCase {
    /// Set to 1 to regenerate the vectors in place (SwiftPM tests aren't sandboxed).
    static let writeFlag = "LACHING_WRITE_CONFORMANCE"

    static let suite: ConformanceSuite = {
        typealias C = CompanionCases
        let root = ConformanceRoot.find(markerFile: "conformance/README.md", from: #filePath)!.appendingPathComponent("companion")
        let hint = "Regenerate with scripts/conformance.sh and commit the result."
        // An app's suite, plus the mechanism only the package pins: the cadence and the desktop presence.
        let app = ConformanceSuite.companion(C.self, root: root, regenerateHint: hint)
        return ConformanceSuite(
            root: root,
            generatedFolders: app.generatedFolders + [CadenceVector.directory, CharacterPoseVector.directory],
            generate: {
                var files = try app.generate()
                files.merge(try CadenceVector.files(cases: C.cadence)) { a, _ in a }
                files.merge(try DesktopVector.files(timelines: C.desktopTimelines, bubbleTexts: C.bubbleTexts, partDelays: C.partDelays)) {
                    a, _ in a
                }
                return files
            },
            replayers: app.replayers + [
                .init(path: CadenceVector.directory) { try CadenceVector.verify($0, file: $1) },
                .init(path: DesktopVector.file) { try DesktopVector.verify($0, file: $1) },
            ],
            regenerateHint: hint)
    }()

    func testVectorsAreUpToDate() throws {
        let problems = try Self.suite.checkUpToDate()
        XCTAssertEqual(problems, [], problems.joined(separator: "\n"))
    }

    /// Replays the committed vectors the way a port does: read the input, run the code, compare.
    func testCommittedVectorsReplay() throws {
        let problems = try Self.suite.replayCommitted()
        XCTAssertEqual(problems, [], problems.joined(separator: "\n"))
    }

    func testGeneratorsAreDeterministic() throws {
        XCTAssertEqual(try Self.suite.checkDeterministic(), [])
    }

    /// The new cases show what they were added for.
    func testTheCompanionCasesPinTheirBehavior() throws {
        let files = try Self.suite.generate()
        func picker(_ name: String) throws -> PickerVector.Vector {
            try ConformanceSupport.decode(PickerVector.Vector.self, from: XCTUnwrap(files["picker/\(name).json"]))
        }
        XCTAssertEqual(try picker("sequence-picked").candidates, ["joke-1"])
        XCTAssertFalse(try picker("skin-restricted-dropped").candidates.contains("skin-raster-only"))
        XCTAssertTrue(try picker("skin-restricted-kept").candidates.contains("skin-raster-only"))
        XCTAssertFalse(try picker("language-scoped-dropped").candidates.contains("it-only"))
        XCTAssertTrue(try picker("language-scoped-kept").candidates.contains("it-only"))
        let quiet = try ConformanceSupport.decode(QuietTimeline.Vector.self, from: XCTUnwrap(files["quiet/sequence-delivered.json"]))
        let ids = quiet.steps.flatMap(\.deliveries).map(\.id)
        XCTAssertTrue(ids.contains("joke-1"), "\(ids)")
        let italian = try ConformanceSupport.decode(QuietTimeline.Vector.self, from: XCTUnwrap(files["quiet/italian-afternoon.json"]))
        XCTAssertEqual(italian.language, "it")
        XCTAssertTrue(italian.steps.flatMap(\.deliveries).contains { ["it-only", "joke-it"].contains($0.id) })
        let raster = try ConformanceSupport.decode(
            CharacterPoseVector.Vector.self, from: XCTUnwrap(files["characters/fixture-raster-poses.json"]))
        XCTAssertNil(raster.shapes)
        XCTAssertEqual(raster.layers?.first?.layers.first?.kind, "image")
        let desktop = try ConformanceSupport.decode(DesktopVector.Vector.self, from: XCTUnwrap(files["desktop.json"]))
        XCTAssertEqual(desktop.partDelays?.first?.seconds, PhraseTiming.partBeat)
    }

    /// Rewrites `conformance/companion` from the Swift reference.
    func testWriteVectors() throws {
        try XCTSkipUnless(
            try Self.suite.writeIfAsked(flag: Self.writeFlag), "Set \(Self.writeFlag)=1 to regenerate the conformance vectors.")
        print("Wrote companion conformance vectors to \(Self.suite.root.path)")
    }
}
