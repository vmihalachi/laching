using System.Numerics;
using Laching.Companion.Shell.App;
using Laching.Companion.Shell.Desktop;
using Laching.Windows.Companion.Shell;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using FigureRect = Laching.Companion.Figures.Rect;
using XamlPath = Microsoft.UI.Xaml.Shapes.Path;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// A sticker card with a tail toward the character (Swift's <c>SpeechBubble</c>), in the look's display face at the
/// control's <c>FontSize</c> (16 by default), in the look's colors. While the character thinks (no text) it shows "…".
/// It fills the width it's given, or hugs its words when <see cref="FillsWidth"/> is false (wrapping only when they
/// don't fit; <c>MaxWidth</c> caps it); wrapped lines come out of even length (<see cref="BalancedWrap"/>). With a <see cref="Countdown"/>, a tiny dial in its bottom-trailing corner runs
/// down until the bubble goes away (on the compositor, so it costs the UI thread nothing); <see cref="RoomForDial"/>
/// keeps the words clear of that corner (set it where a countdown can appear, so the size doesn't change when it does).
/// New words fade in over 0.15 s, unless Windows' animation effects are off. A <see cref="Header"/> sits above the words
/// in the same card (a joke's setup over its punchline), and <see cref="OutlineThickness"/> draws a hairline instead.
/// With <see cref="Announces"/>, Narrator hears each new line without focus moving; the "…" is never read.
/// </summary>
public sealed partial class SpeechBubble : UserControl
{
    /// <summary>The dial's diameter, from the card's bottom and trailing edges, and the words' extra trailing room.</summary>
    private const double DialDiameter = 10;
    private const double DialInset = 9;
    private const double DialRoom = 11;
    private const float DialLine = 2.5f;

    private readonly Grid frame = new();
    private readonly Canvas backdrop = new();
    private readonly Canvas shadowHost = new();
    private readonly XamlPath outline = new() { StrokeThickness = 2, StrokeLineJoin = PenLineJoin.Round };
    private readonly Grid card = new();
    private readonly StackPanel lines = new() { Spacing = Space.Tight };
    private readonly TextBlock words = new() { TextWrapping = TextWrapping.WrapWholeWords, TextAlignment = TextAlignment.Left };
    private readonly Canvas dialHost = new()
    {
        Width = DialDiameter,
        Height = DialDiameter,
        HorizontalAlignment = HorizontalAlignment.Right,
        VerticalAlignment = VerticalAlignment.Bottom,
        Margin = new Thickness(0, 0, DialInset, DialInset),
        Visibility = Visibility.Collapsed,
    };
    private string? text;
    private UIElement? header;
    private BubbleTail tail = BubbleTail.Leading;
    private bool fillsWidth = true;
    private bool roomForDial;
    private BubbleCountdown? countdown;
    private DropShadow? shadow;
    private CompositionColorBrush? dialTrack;
    private CompositionColorBrush? dialArc;
    private CompositionEllipseGeometry? dialArcGeometry;

    public SpeechBubble()
    {
        FontSize = 16;
        IsTabStop = false;
        backdrop.Children.Add(shadowHost);
        backdrop.Children.Add(outline);
        lines.Children.Add(new BalancedLines { Children = { words } });
        card.Children.Add(lines);
        frame.Children.Add(backdrop);
        frame.Children.Add(card);
        frame.Children.Add(dialHost);
        Content = frame;
        AutomationProperties.SetAccessibilityView(backdrop, AccessibilityView.Raw);
        AutomationProperties.SetAccessibilityView(dialHost, AccessibilityView.Raw);
        ShowWords(null);
        Layout();
        backdrop.SizeChanged += (_, _) => Reshape();
        Loaded += (_, _) =>
        {
            MakeShadow();
            ShowCountdown();
        };
        LookBinding.Keep(this, Repaint);
    }

    /// <summary>The words; null or empty shows "…" (the character is thinking). <c>x:Bind</c> passes a null string as empty.</summary>
    public string? Text
    {
        get => text;
        set
        {
            if (text == value)
                return;
            var announcement = Announcement(Announces, text, value);
            text = value;
            ShowWords(value);
            FadeIn();
            Announcer.Say(words, announcement, Announcer.Line);
        }
    }

    /// <summary>
    /// True has Narrator hear each new line as it appears (a polite notification, without moving focus) while the bubble
    /// is on screen; false (the default) keeps it quiet. Opt in on the one bubble that should speak, such as Home's:
    /// every bubble shares one activity id and only the latest notification is read, but one opt-in keeps the same line
    /// from being queued once per view. The words are the line itself; it never announces the "…" or the header.
    /// </summary>
    public bool Announces { get; set; }

    /// <summary>
    /// What a bubble announces when its words go from <paramref name="shown"/> to <paramref name="next"/>: the new
    /// words when it <paramref name="announces"/> and they changed to a line; null otherwise (no opt-in, the same words,
    /// or the "…" of no line).
    /// </summary>
    public static string? Announcement(bool announces, string? shown, string? next) =>
        announces && !string.IsNullOrEmpty(next) && next != shown ? next : null;

    /// <summary>
    /// Something above the words, inside the card (a joke's setup, small, over its punchline); null for none. It keeps
    /// its own look: the bubble sets only the words' font and color.
    /// </summary>
    public UIElement? Header
    {
        get => header;
        set
        {
            if (header == value)
                return;
            if (header is not null)
                lines.Children.Remove(header);
            header = value;
            if (value is not null)
                lines.Children.Insert(0, value);
        }
    }

    /// <summary>The outline's width: 2 by default, 1 for a hairline.</summary>
    public double OutlineThickness
    {
        get => outline.StrokeThickness;
        set => outline.StrokeThickness = value;
    }

    /// <summary>The side the tail comes out of, toward the character; leading by default.</summary>
    public BubbleTail Tail
    {
        get => tail;
        set
        {
            tail = value;
            Layout();
        }
    }

    /// <summary>True (the default) fills the width given; false hugs the words.</summary>
    public bool FillsWidth
    {
        get => fillsWidth;
        set
        {
            fillsWidth = value;
            Layout();
        }
    }

    /// <summary>Keeps the words clear of the countdown dial's corner, even while there's no countdown.</summary>
    public bool RoomForDial
    {
        get => roomForDial;
        set
        {
            roomForDial = value;
            Layout();
        }
    }

    /// <summary>When the bubble started running down and for how long; null hides the dial.</summary>
    public BubbleCountdown? Countdown
    {
        get => countdown;
        set
        {
            if (countdown == value)
                return;
            countdown = value;
            ShowCountdown();
        }
    }

    /// <summary>The words, or "…" hidden from UI Automation while there's no line (it isn't something said).</summary>
    private void ShowWords(string? value)
    {
        var thinking = string.IsNullOrEmpty(value);
        words.Text = thinking ? "…" : value;
        AutomationProperties.SetAccessibilityView(words, thinking ? AccessibilityView.Raw : AccessibilityView.Content);
    }

    private void Layout()
    {
        var t = BubbleMetrics.TailLength;
        var room = tail switch
        {
            BubbleTail.Leading => new Thickness(t, 0, 0, 0),
            BubbleTail.Trailing => new Thickness(0, 0, t, 0),
            BubbleTail.Top => new Thickness(0, t, 0, 0),
            _ => new Thickness(0, 0, 0, t),
        };
        frame.Padding = room;
        frame.HorizontalAlignment = fillsWidth ? HorizontalAlignment.Stretch : HorizontalAlignment.Left;
        frame.VerticalAlignment = VerticalAlignment.Top;
        // The outline's canvas reaches over the tail's room, so the whole outline has positive coordinates.
        backdrop.Margin = new Thickness(-room.Left, -room.Top, -room.Right, -room.Bottom);
        card.Padding = new Thickness(Space.Item, Space.Snug + 2, Space.Item + (roomForDial ? DialRoom : 0), Space.Snug + 2);
        Reshape();
    }

    /// <summary>The outline for the backdrop's size: the card where the tail's room isn't.</summary>
    private void Reshape()
    {
        var (width, height) = (backdrop.ActualWidth, backdrop.ActualHeight);
        if (width <= 0 || height <= 0)
            return;
        var t = BubbleMetrics.TailLength;
        var cardFrame = tail switch
        {
            BubbleTail.Leading => new FigureRect(t, 0, width - t, height),
            BubbleTail.Trailing => new FigureRect(0, 0, width - t, height),
            BubbleTail.Top => new FigureRect(0, t, width, height - t),
            _ => new FigureRect(0, 0, width, height - t),
        };
        outline.Data = Geometries.Path(BubbleShape.Outline(cardFrame, tail));
        if (ElementCompositionPreview.GetElementChildVisual(shadowHost) is { } sprite)
            sprite.Size = new Vector2((float)width, (float)height);
    }

    /// <summary>A soft shadow under the card and tail, masked by the outline (Swift's 2-point shadow, 2 points down).</summary>
    private void MakeShadow()
    {
        if (shadow is not null)
            return;
        var compositor = ElementCompositionPreview.GetElementVisual(shadowHost).Compositor;
        shadow = compositor.CreateDropShadow();
        shadow.BlurRadius = 4;
        shadow.Offset = new Vector3(0, 2, 0);
        shadow.Mask = outline.GetAlphaMask();
        var sprite = compositor.CreateSpriteVisual();
        sprite.Shadow = shadow;
        sprite.Size = new Vector2((float)backdrop.ActualWidth, (float)backdrop.ActualHeight);
        ElementCompositionPreview.SetElementChildVisual(shadowHost, sprite);
        Repaint();
    }

    /// <summary>
    /// The dial: an <c>accentSoft</c> ring whose <c>accent</c> arc drains clockwise from twelve o'clock as the countdown
    /// runs out, animated by the compositor from what is left now.
    /// </summary>
    private void ShowCountdown()
    {
        if (countdown is not { } c)
        {
            dialHost.Visibility = Visibility.Collapsed;
            return;
        }
        dialHost.Visibility = Visibility.Visible;
        if (!IsLoaded)
            return;
        var compositor = ElementCompositionPreview.GetElementVisual(dialHost).Compositor;
        if (dialArcGeometry is null)
        {
            var center = new Vector2((float)DialDiameter / 2);
            var radius = new Vector2((float)DialDiameter / 2 - DialLine / 2);
            var ring = compositor.CreateEllipseGeometry();
            (ring.Center, ring.Radius) = (center, radius);
            dialTrack = compositor.CreateColorBrush();
            var track = compositor.CreateSpriteShape(ring);
            (track.StrokeBrush, track.StrokeThickness) = (dialTrack, DialLine);
            dialArcGeometry = compositor.CreateEllipseGeometry();
            (dialArcGeometry.Center, dialArcGeometry.Radius) = (center, radius);
            dialArc = compositor.CreateColorBrush();
            var arc = compositor.CreateSpriteShape(dialArcGeometry);
            (arc.StrokeBrush, arc.StrokeThickness) = (dialArc, DialLine);
            (arc.StrokeStartCap, arc.StrokeEndCap) = (CompositionStrokeCap.Round, CompositionStrokeCap.Round);
            (arc.CenterPoint, arc.RotationAngleInDegrees) = (center, -90);
            var shapes = compositor.CreateShapeVisual();
            shapes.Size = new Vector2((float)DialDiameter);
            shapes.Shapes.Add(track);
            shapes.Shapes.Add(arc);
            ElementCompositionPreview.SetElementChildVisual(dialHost, shapes);
            Repaint();
        }
        var (left, remaining) = DialLeft(c.Seconds, DateTimeOffset.Now - c.Start);
        dialArcGeometry.StopAnimation(nameof(CompositionGeometry.TrimStart));
        dialArcGeometry.TrimStart = (float)(1 - remaining);
        if (left > 0)
        {
            var drain = compositor.CreateScalarKeyFrameAnimation();
            drain.InsertKeyFrame(0, (float)(1 - remaining));
            drain.InsertKeyFrame(1, 1, compositor.CreateLinearEasingFunction());
            drain.Duration = TimeSpan.FromSeconds(left);
            dialArcGeometry.StartAnimation(nameof(CompositionGeometry.TrimStart), drain);
        }
    }

    /// <summary>
    /// The seconds the dial has left and the share of the ring still drawn, <paramref name="elapsed"/> into a countdown of
    /// <paramref name="seconds"/>. Both are clamped (0 to <paramref name="seconds"/>, and 0 to 1): the countdown is stamped
    /// with the model's clock and measured here with the wall clock, so a clock that runs ahead (a test or fixture clock
    /// that skips) or a time sync that moves the wall clock back gives a negative elapsed time, and the compositor refuses
    /// a <c>TrimStart</c> outside 0 to 1 by throwing, which ends the app.
    /// </summary>
    internal static (double Left, double Remaining) DialLeft(double seconds, TimeSpan elapsed)
    {
        if (seconds <= 0)
            return (0, 0);
        var left = Math.Clamp(seconds - elapsed.TotalSeconds, 0, seconds);
        return (left, left / seconds);
    }

    private void FadeIn()
    {
        if (!IsLoaded || ReduceMotion.IsOn)
            return;
        var visual = ElementCompositionPreview.GetElementVisual(words);
        var fade = visual.Compositor.CreateScalarKeyFrameAnimation();
        fade.InsertKeyFrame(0, 0);
        fade.InsertKeyFrame(1, 1, visual.Compositor.CreateCubicBezierEasingFunction(new Vector2(0, 0), new Vector2(0.58f, 1)));
        fade.Duration = TimeSpan.FromSeconds(0.15);
        visual.StartAnimation(nameof(Visual.Opacity), fade);
    }

    private void Repaint()
    {
        var look = CompanionLook.Current;
        var palette = look.PaletteNow;
        var theme = ActualTheme;
        outline.Fill = new SolidColorBrush(palette.Card.For(theme));
        outline.Stroke = new SolidColorBrush(palette.Outline.For(theme));
        words.Foreground = new SolidColorBrush(palette.Ink.For(theme));
        if (look.DisplayFont is { } font)
            words.FontFamily = font;
        words.FontWeight = look.DisplayWeight;
        if (shadow is not null)
            shadow.Color = palette.Shade.For(theme);
        if (dialTrack is not null && dialArc is not null)
        {
            dialTrack.Color = palette.AccentSoft.For(theme);
            dialArc.Color = palette.Accent.For(theme);
        }
    }
}
