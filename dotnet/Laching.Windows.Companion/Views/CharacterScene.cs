using System.Numerics;
using Laching.Companion.Figures;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// The arithmetic between a rig's placed parts and <see cref="CharacterCanvas"/>'s elements: each part is a canvas the
/// size of the view box whose composition visual takes the part's transform as a <see cref="Matrix4x4"/>, image parts sit
/// in their frame inside it, and whether a point is drawn comes from the shapes and the images' alpha under the same
/// transforms (XAML's own hit test doesn't see a visual's transform). Pure, so the tests run without WinUI.
/// </summary>
public static class CharacterScene
{
    /// <summary>Cubics are flattened into this many lines for the hit test: well under a pixel off at any size drawn.</summary>
    private const int CurveSteps = 16;

    /// <summary>
    /// The rig's transform for a composition visual's <c>TransformMatrix</c> (row vectors: x' = M11 x + M21 y + M41,
    /// y' = M12 x + M22 y + M42), the same affine as <see cref="Transform"/>'s a, b, c, d, tx, ty.
    /// </summary>
    public static Matrix4x4 Matrix(Transform transform) =>
        new(
            (float)transform.A,
            (float)transform.B,
            0,
            0,
            (float)transform.C,
            (float)transform.D,
            0,
            0,
            0,
            0,
            1,
            0,
            (float)transform.Tx,
            (float)transform.Ty,
            0,
            1
        );

    /// <summary>Where <paramref name="point"/> lands under <paramref name="matrix"/>, as the compositor maps it.</summary>
    internal static Point Apply(Matrix4x4 matrix, Point point)
    {
        var v = Vector2.Transform(new Vector2((float)point.X, (float)point.Y), matrix);
        return new(v.X, v.Y);
    }

    /// <summary>Points per view-box unit when the view box fits <paramref name="width"/> × <paramref name="height"/> (centered, as a uniform <c>Viewbox</c>).</summary>
    public static double FitScale(Size viewBox, double width, double height) =>
        viewBox.Width > 0 && viewBox.Height > 0 ? Math.Max(0, Math.Min(width / viewBox.Width, height / viewBox.Height)) : 0;

    /// <summary>A part's extent in view-box units before the rig moves it: its shapes' points (control points too) or its image's frame.</summary>
    public static Rect Bounds(PartContent content)
    {
        if (content is PartContent.Image image)
            return image.Frame;
        var points = ((PartContent.Vector)content).Shapes.SelectMany(s => s.Path).SelectMany(c => c.Points).ToArray();
        if (points.Length == 0)
            return new Rect(0, 0, 0, 0);
        var (minX, minY) = (points.Min(p => p.X), points.Min(p => p.Y));
        return new Rect(minX, minY, points.Max(p => p.X) - minX, points.Max(p => p.Y) - minY);
    }

    /// <summary>
    /// A box around each of <paramref name="layers"/> as placed (view-box units) that holds all it draws: its shapes with
    /// half their widest stroke, or its image's frame, under the rig's transform. Never smaller than the drawing, a
    /// little bigger for a turned part; a layer with nothing in it gives none. The desktop window shapes where a finger
    /// reaches from these.
    /// </summary>
    public static IReadOnlyList<Rect> DrawnBounds(IReadOnlyList<PlacedPart> layers)
    {
        var boxes = new List<Rect>(layers.Count);
        foreach (var layer in layers)
        {
            if (layer.Content is PartContent.Vector { Shapes.Count: 0 })
                continue;
            var bounds = Bounds(layer.Content);
            var pad = layer.Content is PartContent.Vector vector ? vector.Shapes.Max(s => s.Stroke is null ? 0 : s.StrokeWidth / 2) : 0;
            var padded = new Rect(bounds.Origin.X - pad, bounds.Origin.Y - pad, bounds.Size.Width + 2 * pad, bounds.Size.Height + 2 * pad);
            var corners = padded.Corners.Select(layer.Transform.Apply).ToArray();
            var (minX, minY) = (corners.Min(p => p.X), corners.Min(p => p.Y));
            boxes.Add(new Rect(minX, minY, corners.Max(p => p.X) - minX, corners.Max(p => p.Y) - minY));
        }
        return boxes;
    }

    /// <summary>
    /// How far, in view-box units, any point of <paramref name="bounds"/> moves from <paramref name="from"/> to
    /// <paramref name="to"/>: the farthest corner, as the difference of two affines is affine.
    /// </summary>
    public static double Shift(Transform from, Transform to, Rect bounds)
    {
        var most = 0.0;
        foreach (var corner in bounds.Corners)
        {
            var (a, b) = (from.Apply(corner), to.Apply(corner));
            most = Math.Max(most, Math.Sqrt((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y)));
        }
        return most;
    }

    /// <summary>
    /// Where <paramref name="point"/> (view-box units) falls on an image part placed by <paramref name="transform"/> in
    /// <paramref name="frame"/>: 0 to 1 across and down the image, or null when it misses the image.
    /// </summary>
    public static (double U, double V)? ImagePoint(Transform transform, Rect frame, Point point)
    {
        var det = transform.A * transform.D - transform.B * transform.C;
        if (Math.Abs(det) < 1e-12 || frame.Size.Width <= 0 || frame.Size.Height <= 0)
            return null;
        var (x, y) = (point.X - transform.Tx, point.Y - transform.Ty);
        var local = new Point((transform.D * x - transform.C * y) / det, (-transform.B * x + transform.A * y) / det);
        var u = (local.X - frame.Origin.X) / frame.Size.Width;
        var v = (local.Y - frame.Origin.Y) / frame.Size.Height;
        return u is >= 0 and < 1 && v is >= 0 and < 1 ? (u, v) : null;
    }

    /// <summary>
    /// Whether any of <paramref name="layers"/> draws at <paramref name="point"/> (view-box units): inside a filled shape
    /// (by its fill rule), within half a stroke's width of a stroked one (<paramref name="slop"/> more, in view-box units,
    /// for a thin line), or on an image pixel with some alpha (<paramref name="mask"/> by asset; no mask, no hit).
    /// Shapes nearly transparent don't count.
    /// </summary>
    public static bool IsDrawnAt(IReadOnlyList<PlacedPart> layers, Point point, Func<string, AlphaMask?> mask, double slop = 0)
    {
        for (var i = layers.Count - 1; i >= 0; i--)
        {
            var layer = layers[i];
            switch (layer.Content)
            {
                case PartContent.Vector vector:
                    foreach (var shape in vector.Shapes)
                        if (shape.Opacity > 0.05 && Hits(shape.Transformed(layer.Transform), point, slop))
                            return true;
                    break;
                case PartContent.Image image:
                    if (ImagePoint(layer.Transform, image.Frame, point) is { } at && mask(image.Asset) is { } alpha && alpha.IsDrawn(at.U, at.V))
                        return true;
                    break;
            }
        }
        return false;
    }

    private static bool Hits(PathShape shape, Point point, double slop)
    {
        var figures = Flattened(shape.Path);
        if (shape.Fill is not null && Inside(figures, point, shape.EvenOdd))
            return true;
        if (shape.Stroke is null || shape.StrokeWidth <= 0)
            return false;
        var reach = shape.StrokeWidth / 2 + slop;
        foreach (var (points, closed) in figures)
        {
            var count = closed ? points.Count : points.Count - 1;
            for (var k = 0; k < count; k++)
                if (Distance(point, points[k], points[(k + 1) % points.Count]) <= reach)
                    return true;
        }
        return false;
    }

    /// <summary>Each figure of the path as a polyline, and whether it was closed.</summary>
    private static List<(List<Point> Points, bool Closed)> Flattened(IReadOnlyList<PathCommand> path)
    {
        var figures = new List<(List<Point>, bool)>();
        List<Point>? current = null;
        var closed = false;
        void Finish()
        {
            if (current is { Count: > 1 })
                figures.Add((current, closed));
        }
        foreach (var command in path)
        {
            switch (command)
            {
                case PathCommand.Move move:
                    Finish();
                    (current, closed) = ([move.To], false);
                    break;
                case PathCommand.Line line when current is not null:
                    current.Add(line.To);
                    break;
                case PathCommand.Cubic cubic when current is not null:
                    var p0 = current[^1];
                    for (var s = 1; s <= CurveSteps; s++)
                        current.Add(Bezier(p0, cubic.Control1, cubic.Control2, cubic.To, (double)s / CurveSteps));
                    break;
                case PathCommand.Close when current is not null:
                    closed = true;
                    Finish();
                    (current, closed) = ([current[0]], false);
                    break;
            }
        }
        Finish();
        return figures;
    }

    /// <summary>A fill covers every figure, closed or not, as renderers fill an open subpath by closing it.</summary>
    private static bool Inside(List<(List<Point> Points, bool Closed)> figures, Point p, bool evenOdd)
    {
        var winding = 0;
        foreach (var (points, _) in figures)
        {
            for (var k = 0; k < points.Count; k++)
            {
                var (a, b) = (points[k], points[(k + 1) % points.Count]);
                if (a.Y <= p.Y)
                {
                    if (b.Y > p.Y && Cross(a, b, p) > 0)
                        winding++;
                }
                else if (b.Y <= p.Y && Cross(a, b, p) < 0)
                    winding--;
            }
        }
        return evenOdd ? winding % 2 != 0 : winding != 0;
    }

    private static double Cross(Point a, Point b, Point p) => (b.X - a.X) * (p.Y - a.Y) - (p.X - a.X) * (b.Y - a.Y);

    private static double Distance(Point p, Point a, Point b)
    {
        var (dx, dy) = (b.X - a.X, b.Y - a.Y);
        var length = dx * dx + dy * dy;
        var t = length == 0 ? 0 : Math.Clamp(((p.X - a.X) * dx + (p.Y - a.Y) * dy) / length, 0, 1);
        var (x, y) = (a.X + t * dx - p.X, a.Y + t * dy - p.Y);
        return Math.Sqrt(x * x + y * y);
    }

    private static Point Bezier(Point p0, Point c1, Point c2, Point p3, double t)
    {
        var u = 1 - t;
        double At(double a, double b, double c, double d) => u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
        return new(At(p0.X, c1.X, c2.X, p3.X), At(p0.Y, c1.Y, c2.Y, p3.Y));
    }
}
