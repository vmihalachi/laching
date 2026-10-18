import Foundation

/// One part placed for drawing: its content in view-box units and the transform the rig gives it.
public struct PlacedPart: Equatable, Sendable {
    public let name: String
    public let transform: Transform
    public let content: PartContent

    public init(name: String, transform: Transform, content: PartContent) {
        self.name = name
        self.transform = transform
        self.content = content
    }
}
