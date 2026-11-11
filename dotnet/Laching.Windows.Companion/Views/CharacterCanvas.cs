using System.Diagnostics;
using Laching.Companion.Figures;
using Laching.Companion.Rules;
using Laching.Windows.Companion.Screenshots;
using Laching.Windows.Companion.Shell;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using FigurePoint = Laching.Companion.Figures.Point;
using IOPath = System.IO.Path;
using Transform = Laching.Companion.Figures.Transform;
using XamlPath = Microsoft.UI.Xaml.Shapes.Path;
using XamlPoint = Windows.Foundation.Point;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// A character drawn from its compiled art (Swift's <c>CharacterView</c>), the view box fitted and centered: vector parts
/// as <see cref="XamlPath"/>s in the <see cref="Palette"/>'s paints, raster parts as <see cref="Image"/>s from
/// <see cref="ImageFolder"/>, each part moved by the rig through its composition visual. The pose (sway, blink, the
/// talking mouth and the pulse) comes from the mood and the seconds since the canvas appeared, at up to 30 frames a
/// second; it holds the still pose when <see cref="Animated"/> is false, Windows' animation effects are off or a
/// screenshot run is going, and stops while its window is hidden. Waking from Shh springs from the drooped pose back
/// over 0.6 s. While talking, <see cref="MouthLevel"/> (read every frame; the performer's <c>MouthLevel</c>) moves the
/// mouth with a recorded clip's loudness; null keeps the mood's timer. Narrator reads <see cref="Line"/>, else
/// <see cref="AccessibilityLabel"/>.
/// </summary>
/// <remarks>
/// Each part's elements are made once; a frame only sets the transforms of the parts that moved (on the visual, so XAML
/// doesn't lay out or render anything again) and, when the eyes or mouth change, which parts are in the stage. A frame
/// that moves nothing by half a screen pixel is skipped, and the idle sway runs at 15 frames a second (talking at 30).
/// The plan's 4.1 to 4.3 outcomes have the measurements.
/// </remarks>
public sealed partial class CharacterCanvas : UserControl
{
    private readonly Viewbox viewbox = new() { Stretch = Stretch.Uniform };
    private readonly Canvas stage = new();
    private readonly Dictionary<string, PartView> parts = [];
    private readonly List<string> order = [];
    private readonly Dictionary<(string Role, ElementTheme Theme), SolidColorBrush> brushes = [];
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private readonly Action frame;
    private RiggedCharacter? character;
    private Mood mood = Mood.Awake;
    private bool talking;
    private bool animated = true;
    private string? line;
    private string? accessibilityLabel;
    private CompanionPalette? palette;
    private string imageFolder = IOPath.Combine(AppContext.BaseDirectory, "Assets", "Characters");
    /// <summary>A frame that moves nothing by this many screen pixels or more is skipped: the slow sway draws at half the rate or less.</summary>
    private const double SkipBelowPixels = 0.5;

    private bool moving;
    private bool fast;
    private double? wokeAt;
    private Pose? drawn;
    private IReadOnlyList<PlacedPart> layers = [];
    private double imageScale;

    public CharacterCanvas()
    {
        frame = OnFrame;
        viewbox.Child = stage;
        Content = viewbox;
        IsTabStop = false;
        AutomationProperties.SetAccessibilityView(viewbox, AccessibilityView.Raw);
        Loaded += (_, _) =>
        {
            if (XamlRoot is { } root)
                root.Changed += OnRootChanged;
            RefreshImages();
            Draw(force: true);
            UpdateMoving();
        };
        Unloaded += (_, _) =>
        {
            if (XamlRoot is { } root)
                root.Changed -= OnRootChanged;
            StopMoving();
        };
        SizeChanged += (_, _) => RefreshImages();
        LookBinding.Keep(this, Repaint);
    }

    /// <summary>The character drawn; nothing without one.</summary>
    public RiggedCharacter? Character
    {
        get => character;
        set
        {
            if (ReferenceEquals(character, value))
                return;
            character = value;
            Rebuild();
        }
    }

    public Mood Mood
    {
        get => mood;
        set
        {
            if (mood == value)
                return;
            var was = CharacterMotion.Shown(mood, talking);
            mood = value;
            ShownMoodChanged(was);
        }
    }

    /// <summary>The voice is playing: the talking mood, its mouth moving.</summary>
    public bool Talking
    {
        get => talking;
        set
        {
            if (talking == value)
                return;
            var was = CharacterMotion.Shown(mood, talking);
            talking = value;
            ShownMoodChanged(was);
        }
    }

    /// <summary>False holds the still pose (a picture of the character, such as in a skin picker).</summary>
    public bool Animated
    {
        get => animated;
        set
        {
            if (animated == value)
                return;
            animated = value;
            Draw(force: true);
            UpdateMoving();
        }
    }

    /// <summary>
    /// The latest line, which Narrator reads instead of <see cref="AccessibilityLabel"/>; null or empty is none. Leave it
    /// unset where a <see cref="SpeechBubble"/> beside the canvas shows the same line, or Narrator reads it twice.
    /// </summary>
    public string? Line
    {
        get => line;
        set
        {
            line = value;
            UpdateName();
        }
    }

    /// <summary>What Narrator says when there's no line, such as "Sunny, asleep" (the app's words).</summary>
    public string? AccessibilityLabel
    {
        get => accessibilityLabel;
        set
        {
            accessibilityLabel = value;
            UpdateName();
        }
    }

    /// <summary>The voice's mouth level (0–3) right now, or null for the mood's timer; read every frame while talking.</summary>
    public Func<int?>? MouthLevel { get; set; }

    /// <summary>The paints; the look's (<see cref="CompanionLook.Current"/>) when unset.</summary>
    public CompanionPalette Palette
    {
        get => palette ?? CompanionLook.Current.Palette;
        set
        {
            palette = value;
            Repaint();
        }
    }

    /// <summary>
    /// Where the raster parts' PNGs are (<c>&lt;skin&gt;.&lt;part&gt;.scale-200.png</c> and so on, see
    /// <see cref="CharacterImages"/>); <c>Assets\Characters</c> next to the app by default.
    /// </summary>
    public string ImageFolder
    {
        get => imageFolder;
        set
        {
            if (imageFolder == value)
                return;
            imageFolder = value;
            Rebuild();
        }
    }

    /// <summary>
    /// Whether the character is drawn at <paramref name="point"/>, in the window's content coordinates (DIPs), in the
    /// pose on screen: a vector part's fill or stroke, or a raster part's pixel with some alpha. The desktop window's
    /// click-through asks this. UI thread only.
    /// </summary>
    public bool IsDrawnAt(XamlPoint point)
    {
        if (XamlRoot is null || character is not { } c || layers.Count == 0)
            return false;
        var at = stage.TransformToVisual(null).Inverse.TransformPoint(point);
        // Half a point of slop, so a hairline stays clickable.
        var fit = CharacterScene.FitScale(c.ViewBox, ActualWidth, ActualHeight);
        var slop = fit > 0 ? 0.5 / fit : 0;
        return CharacterScene.IsDrawnAt(layers, new FigurePoint(at.X, at.Y), Mask, slop);
    }

    /// <summary>
    /// Boxes that hold everything the character draws in the pose on screen, one per part, in the window's content
    /// coordinates (DIPs): never smaller than the drawing (<see cref="CharacterScene.DrawnBounds"/>). The desktop window
    /// shapes where a finger reaches from these. UI thread only.
    /// </summary>
    public IReadOnlyList<Rect> DrawnBounds()
    {
        if (XamlRoot is null || layers.Count == 0)
            return [];
        var toWindow = stage.TransformToVisual(null);
        return CharacterScene
            .DrawnBounds(layers)
            .Select(b => toWindow.TransformBounds(new global::Windows.Foundation.Rect(b.Origin.X, b.Origin.Y, b.Size.Width, b.Size.Height)))
            .Select(b => new Rect(b.X, b.Y, b.Width, b.Height))
            .ToArray();
    }

    protected override AutomationPeer OnCreateAutomationPeer() => new CharacterCanvasPeer(this);

    private bool IsStill => !animated || ReduceMotion.IsOn || ScreenshotCapture.IsEnabled;

    private bool IsShowing => IsLoaded && Visibility == Visibility.Visible && XamlRoot is { IsHostVisible: true };

    private double Now => clock.Elapsed.TotalSeconds;

    private void ShownMoodChanged(Mood was)
    {
        var now = CharacterMotion.Shown(mood, talking);
        if (was == Mood.Shushed && now != Mood.Shushed && !IsStill)
            wokeAt = Now;
        Draw(force: true);
        UpdateMoving();
    }

    private void OnRootChanged(XamlRoot sender, XamlRootChangedEventArgs args)
    {
        RefreshImages();
        UpdateMoving();
        if (IsShowing)
            Draw(force: true);
    }

    /// <summary>
    /// On the shared frame clock while the character may move and shows: fast while talking or springing back from Shh,
    /// slow for the idle sway.
    /// </summary>
    private void UpdateMoving()
    {
        if (IsShowing && !IsStill && character is not null)
        {
            var wantsFast = talking || wokeAt is not null;
            if (moving && fast == wantsFast)
                return;
            (moving, fast) = (true, wantsFast);
            FrameClock.Add(frame, fast);
        }
        else
            StopMoving();
    }

    private void StopMoving()
    {
        if (!moving)
            return;
        moving = false;
        FrameClock.Remove(frame);
    }

    private void OnFrame()
    {
        if (IsStill || !IsShowing)
        {
            Draw(force: true);
            UpdateMoving();
            return;
        }
        Draw(force: false);
    }

    /// <summary>Starts over with the character's parts, for a new character or image folder.</summary>
    private void Rebuild()
    {
        stage.Children.Clear();
        parts.Clear();
        order.Clear();
        layers = [];
        drawn = null;
        wokeAt = null;
        if (character is { } c)
        {
            stage.Width = c.ViewBox.Width;
            stage.Height = c.ViewBox.Height;
        }
        imageScale = 0;
        RefreshImages();
        Draw(force: true);
        UpdateMoving();
    }

    private void Draw(bool force)
    {
        if (character is not { } c)
            return;
        var now = Now;
        var still = IsStill;
        var since = wokeAt is { } woke ? now - woke : (double?)null;
        if (since >= CharacterMotion.WakeDuration)
        {
            wokeAt = null;
            UpdateMoving();
        }
        var level = talking && !still ? MouthLevel?.Invoke() : null;
        var pose = CharacterMotion.PoseAt(c, mood, talking, still, now, level, since);
        if (!force && pose.Equals(drawn))
            return;
        var next = c.Layers(pose);
        var sameParts = next.Select(l => l.Name).SequenceEqual(order);
        if (!force && sameParts && MostShift(next) * PixelsPerUnit() < SkipBelowPixels)
            return;
        drawn = pose;
        layers = next;
        if (!sameParts)
        {
            stage.Children.Clear();
            order.Clear();
            foreach (var layer in layers)
            {
                stage.Children.Add(Part(layer).Root);
                order.Add(layer.Name);
            }
        }
        foreach (var layer in layers)
            parts[layer.Name].Place(layer.Transform);
    }

    private PartView Part(PlacedPart layer)
    {
        if (parts.TryGetValue(layer.Name, out var existing))
            return existing;
        var part = layer.Content switch
        {
            PartContent.Vector vector => PartView.Shapes(vector.Shapes, Brush),
            PartContent.Image image => PartView.Picture(image, imageFolder, PixelsPerUnit()),
            _ => throw new InvalidOperationException($"Unknown part content {layer.Content.Kind}"),
        };
        parts[layer.Name] = part;
        return part;
    }

    /// <summary>How far any part of <paramref name="next"/> moves from where it is drawn, in view-box units.</summary>
    private double MostShift(IReadOnlyList<PlacedPart> next)
    {
        var most = 0.0;
        foreach (var layer in next)
            most = Math.Max(most, parts[layer.Name].ShiftTo(layer.Transform));
        return most;
    }

    private AlphaMask? Mask(string asset) =>
        parts.Values.FirstOrDefault(p => p.Asset == asset)?.File is { } file ? CharacterImages.Mask(file) : null;

    /// <summary>The raster parts' pixels per view-box unit on screen: the fit times the display's scale.</summary>
    private double PixelsPerUnit()
    {
        if (character is not { } c)
            return 2;
        var fit = CharacterScene.FitScale(c.ViewBox, ActualWidth, ActualHeight);
        var screen = XamlRoot?.RasterizationScale ?? 1;
        return fit > 0 ? fit * screen : 2;
    }

    /// <summary>Swaps the raster parts' files when the canvas or the display's scale changed enough to want others.</summary>
    private void RefreshImages()
    {
        var scale = PixelsPerUnit();
        if (Math.Abs(scale - imageScale) < 0.01)
            return;
        imageScale = scale;
        foreach (var part in parts.Values)
            part.Reload(imageFolder, scale);
    }

    private void Repaint()
    {
        brushes.Clear();
        foreach (var part in parts.Values)
            part.Repaint(Brush);
    }

    private Brush? Brush(PaintRole? role)
    {
        if (role is not { } r)
            return null;
        var theme = ActualTheme;
        if (!brushes.TryGetValue((r.RawValue, theme), out var brush))
            brushes[(r.RawValue, theme)] = brush = new SolidColorBrush(Palette.Paints(r).For(theme));
        return brush;
    }

    /// <summary>
    /// What Narrator reads: the line, else the label. x:Bind hands a null line over as "", so an empty line counts as
    /// none, as <see cref="SpeechBubble"/>'s words do.
    /// </summary>
    internal static string AccessibleName(string? line, string? accessibilityLabel) =>
        string.IsNullOrEmpty(line) ? accessibilityLabel ?? "" : line;

    private void UpdateName() => AutomationProperties.SetName(this, AccessibleName(line, accessibilityLabel));

    /// <summary>One part: a canvas the size of the view box whose visual takes the part's transform, holding its paths or its image.</summary>
    private sealed class PartView
    {
        private readonly List<(XamlPath Path, PathShape Shape)> paths = [];
        private Visual? visual;
        private PartContent.Image? image;
        private Image? element;
        private Transform placed = Transform.Identity;

        private Rect bounds;

        private PartView() => Root = new Canvas();

        public Canvas Root { get; }

        /// <summary>The raster part's asset, null for a vector part.</summary>
        public string? Asset => image?.Asset;

        /// <summary>The PNG drawn, null for a vector part or a missing file.</summary>
        public string? File { get; private set; }

        public static PartView Shapes(IReadOnlyList<PathShape> shapes, Func<PaintRole?, Brush?> brush)
        {
            var part = new PartView { bounds = CharacterScene.Bounds(new PartContent.Vector(shapes)) };
            foreach (var shape in shapes)
            {
                var path = new XamlPath
                {
                    Data = Geometries.Path(shape.Path, shape.EvenOdd),
                    StrokeThickness = shape.StrokeWidth,
                    StrokeLineJoin = PenLineJoin.Round,
                    StrokeStartLineCap = PenLineCap.Round,
                    StrokeEndLineCap = PenLineCap.Round,
                    Opacity = shape.Opacity,
                };
                part.paths.Add((path, shape));
                part.Root.Children.Add(path);
            }
            part.Repaint(brush);
            return part;
        }

        public static PartView Picture(PartContent.Image image, string folder, double pixelsPerUnit)
        {
            var part = new PartView { image = image, bounds = image.Frame };
            part.element = new Image
            {
                Width = image.Frame.Size.Width,
                Height = image.Frame.Size.Height,
                Stretch = Stretch.Fill,
            };
            Canvas.SetLeft(part.element, image.Frame.Origin.X);
            Canvas.SetTop(part.element, image.Frame.Origin.Y);
            part.Root.Children.Add(part.element);
            part.Reload(folder, pixelsPerUnit);
            return part;
        }

        /// <summary>How far the part would move from where it is drawn to <paramref name="t"/>, in view-box units.</summary>
        public double ShiftTo(Transform t) => visual is null ? double.PositiveInfinity : CharacterScene.Shift(placed, t, bounds);

        public void Place(Transform t)
        {
            if (t == placed && visual is not null)
                return;
            placed = t;
            visual ??= ElementCompositionPreview.GetElementVisual(Root);
            visual.TransformMatrix = CharacterScene.Matrix(t);
        }

        public void Repaint(Func<PaintRole?, Brush?> brush)
        {
            foreach (var (path, shape) in paths)
            {
                path.Fill = brush(shape.Fill);
                path.Stroke = shape.StrokeWidth > 0 ? brush(shape.Stroke) : null;
            }
        }

        public void Reload(string folder, double pixelsPerUnit)
        {
            if (image is null || element is null)
                return;
            var next = CharacterImages.File(folder, image.Asset, pixelsPerUnit);
            if (next == File)
                return;
            File = next;
            element.Source = next is null ? null : new BitmapImage(new Uri(next));
        }
    }

    /// <summary>One image to Narrator, named by the line or the label; the parts inside aren't elements of their own.</summary>
    private sealed partial class CharacterCanvasPeer(CharacterCanvas owner) : FrameworkElementAutomationPeer(owner)
    {
        protected override AutomationControlType GetAutomationControlTypeCore() => AutomationControlType.Image;

        protected override string GetClassNameCore() => nameof(CharacterCanvas);

        protected override IList<AutomationPeer> GetChildrenCore() => [];
    }
}
