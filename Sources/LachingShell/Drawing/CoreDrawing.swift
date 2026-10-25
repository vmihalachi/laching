#if os(macOS) || os(iOS)
    import LachingCore

    // The drawing helpers moved to LachingCore so an app's pure core can sample figures; these keep
    // `import LachingShell` alone enough for the apps that draw.

    /// `LachingCore.SVGPath`: samples SVG path data into polylines.
    public typealias SVGPath = LachingCore.SVGPath
    /// `LachingCore.Wobble`: the seeded hand-drawn wobble for sampled polylines.
    public typealias Wobble = LachingCore.Wobble
    /// `LachingCore.Keyframes`: piecewise-linear keyframe interpolation.
    public typealias Keyframes = LachingCore.Keyframes
    /// `LachingCore.Polyline`: point lists for simple shapes.
    public typealias Polyline = LachingCore.Polyline
#endif
