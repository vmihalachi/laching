import LachingTesting
import XCTest

/// Keeps `conformance/scheduler` in step with the Swift reference. The vectors are generated here and replayed by
/// `dotnet/Laching.Core.Tests`; change one only by regenerating it (`scripts/conformance.sh`), never by hand.
@MainActor
final class ConformanceTests: XCTestCase {
    /// Set to 1 to regenerate the vectors in place (SwiftPM tests aren't sandboxed).
    static let writeFlag = "LACHING_WRITE_CONFORMANCE"

    static let suite = ConformanceSuite(
        root: ConformanceRoot.find(markerFile: "conformance/README.md", from: #filePath)!,
        generatedFolders: [SchedulerVectors.directory],
        generate: { try SchedulerVectors.files() },
        replayers: [.init(path: SchedulerVectors.directory) { data, file in try SchedulerVectors.verify(data, file: file) }],
        regenerateHint: "Regenerate with scripts/conformance.sh and commit the result.")

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

    /// Rewrites `conformance/scheduler` from the Swift reference.
    func testWriteVectors() throws {
        try XCTSkipUnless(
            try Self.suite.writeIfAsked(flag: Self.writeFlag), "Set \(Self.writeFlag)=1 to regenerate the conformance vectors.")
        print("Wrote conformance vectors to \(Self.suite.root.path)")
    }
}
