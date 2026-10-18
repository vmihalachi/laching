namespace Laching.Companion.Figures;

/// <summary>One part placed for drawing: its content in view-box units and the transform the rig gives it.</summary>
public sealed record PlacedPart(string Name, Transform Transform, PartContent Content);
