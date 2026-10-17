using Laching.Companion.Desktop;
using Laching.Companion.Settings;
using Laching.Companion.Shell.App;
using Laching.Companion.Shell.Desktop;
using Laching.Windows.Companion.Shell;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Graphics;
using FigurePoint = Laching.Companion.Figures.Point;
using FigureRect = Laching.Companion.Figures.Rect;
using FigureSize = Laching.Companion.Figures.Size;
using ShellNative = Laching.Windows.Companion.Shell.NativeMethods;
using Win32 = Laching.Windows.Shell.NativeMethods;
using XamlPoint = Windows.Foundation.Point;
using XamlSize = Windows.Foundation.Size;

namespace Laching.Windows.Companion.Desktop;

/// <summary>
/// The character on the desktop (Swift's <c>DesktopCompanion</c>): a <see cref="DesktopWindow"/> with the app's
/// character view standing on a soft contact shadow, and a second one riding along beside it for the speech bubble. A
/// click says something, a drag moves the character (and reports the spot), a right click opens its menu, Ctrl+wheel or
/// a pinch changes the size and a click on the bubble puts it away. Clicks pass through everywhere the character isn't
/// drawn. The model decides when it shows and what it says; the host makes it from the app's character view
/// (<see cref="CompanionViews{TWindow}.DesktopCharacter"/>). Positions are the anchor (the window's bottom middle) in
/// screen pixels, y down. UI thread only; asks for no permission.
/// </summary>
/// <remarks>
/// It has no keyboard path, by design: both windows are no-activate tool windows, so the app in front keeps the
/// keyboard, and neither Tab nor Alt+Tab reaches them. Narrator finds the character as one button (named like its canvas,
/// whose action says something) by touch or the pointer. Its actions are all in the tray icon's menu, which the keyboard
/// reaches (Win+B, then Shift+F10 on the icon): Say something, Shh, and the desktop submenu to show it, hide it for a
/// while and change its size. Only moving it takes the pointer; a keyboard user it is in the way of hides it from there.
/// The tray menu has no position reset: the character only ever stands where a drag left it or in its default corner,
/// both wholly on a display, so there is nothing to recover from the keyboard.
/// </remarks>
public sealed class DesktopCompanion : IDesktopCompanion, IDisposable
{
    /// <summary>The default spot: this far (DIPs) in from the bottom right of the primary display's work area.</summary>
    public const double CornerInset = 24;

    /// <summary>
    /// The theme the character's stand and its bubble draw in: the app's appearance setting, as the host applies it to its
    /// other windows. <see cref="ElementTheme.Default"/> follows Windows' app mode.
    /// </summary>
    public ElementTheme Theme
    {
        get => stand.RequestedTheme;
        set => (stand.RequestedTheme, bubbleRoot.RequestedTheme) = (value, value);
    }

    /// <summary>The desktop bubble's text size.</summary>
    public const double BubbleTextSize = 15;

    private readonly DesktopLayout layout;
    private readonly DesktopWindow character;
    private readonly DesktopWindow bubble;
    private readonly SpeechBubble bubbleView = new() { FillsWidth = false, RoomForDial = true, FontSize = BubbleTextSize };
    private readonly Grid bubbleRoot;
    private readonly DesktopStand stand;
    private readonly TrayIconHost menuHost;
    private readonly DispatcherQueue queue;
    private DesktopSize size;
    private DesktopPosition? position;
    private string? bubbleText;
    private BubbleCountdown? countdown;
    private FigureSize bubbleSize;

    /// <param name="characterView">The app's view of the character (a <see cref="CharacterCanvas"/> bound to its state).</param>
    /// <param name="sayActionName">What Narrator calls the action that says something.</param>
    /// <param name="title">
    /// The windows' title, which Narrator reads for them (the host passes the tray icon's tooltip); null leaves WinUI's.
    /// </param>
    public DesktopCompanion(
        UIElement characterView,
        DesktopLayout layout,
        DesktopSize size,
        DesktopPosition? position,
        string sayActionName,
        string? title = null
    )
    {
        queue = UiThread.Resolve(queue: null, "desktop character");
        this.layout = layout;
        this.size = size;
        this.position = position;
        stand = new DesktopStand(characterView, layout, sayActionName, () => OnSay?.Invoke());
        var windowSize = layout.WindowSize(size);
        character = new DesktopWindow(stand, windowSize, Anchor(position, windowSize)) { HitTest = stand.IsDrawnAt, DrawnBounds = stand.DrawnBounds };
        bubbleRoot = new Grid
        {
            Padding = new Thickness(layout.BubbleMargin),
            HorizontalAlignment = HorizontalAlignment.Left,
            VerticalAlignment = VerticalAlignment.Top,
        };
        bubbleRoot.Children.Add(bubbleView);
        bubble = new DesktopWindow(bubbleRoot, new FigureSize(1, 1), default, isMovable: false);
        bubble.AttachTo(character);
        character.OnClick = () => OnSay?.Invoke();
        character.OnPinch = step => OnPinch?.Invoke(step);
        character.OnMenuAt = ShowMenu;
        character.OnDragging = () => PlaceBubble(measure: false);
        character.OnDragEnded = DragEnded;
        character.OnScreensChanged = () => PlaceBubble(measure: false);
        bubble.OnClick = () =>
        {
            HideBubble(animated: true);
            OnBubbleDismissed?.Invoke();
        };
        // A tray host without a glyph shows no icon: its hidden window owns the menu.
        menuHost = new TrayIconHost(queue, Guid.NewGuid(), "Laching.DesktopCompanion.Menu");
        if (title is not null)
            character.Title = bubble.Title = title;
    }

    public Action? OnSay { get; set; }

    public Action<DesktopPosition>? OnMoved { get; set; }

    public Action<int>? OnPinch { get; set; }

    public Action? OnBubbleDismissed { get; set; }

    public Func<IReadOnlyList<MenuItem>>? Menu { get; set; }

    /// <summary>
    /// Whether the menu's words carry their own access keys ("&amp;Say something"); off by default, so every "&amp;"
    /// shows as written. See <see cref="TrayIconHost.AccessKeys"/>.
    /// </summary>
    public bool MenuAccessKeys
    {
        get => menuHost.AccessKeys;
        set => menuHost.AccessKeys = value;
    }

    public bool IsShown => character.IsShown;

    public void Show(bool animated)
    {
        if (!character.IsShown)
            character.Show(animated);
    }

    public void Hide(bool animated)
    {
        HideBubble(animated);
        character.Hide(animated);
    }

    public void SetSize(DesktopSize size)
    {
        if (size == this.size)
            return;
        this.size = size;
        var windowSize = layout.WindowSize(size);
        if (position is null)
            character.Anchor = Anchor(null, windowSize);
        character.SetSize(windowSize);
        PlaceBubble(measure: false);
    }

    public void SetPosition(DesktopPosition? position)
    {
        if (position == this.position)
            return;
        this.position = position;
        character.Anchor = Anchor(position, character.Size);
        PlaceBubble(measure: false);
    }

    /// <summary>Shows <paramref name="text"/> beside the character (only while it's shown); <paramref name="countdown"/> runs the dial down.</summary>
    public void ShowBubble(string text, BubbleCountdown? countdown, bool animated)
    {
        if (!character.IsShown || (text == bubbleText && countdown == this.countdown && bubble.IsShown))
            return;
        bubbleText = text;
        this.countdown = countdown;
        PlaceBubble(measure: true);
        bubble.Show(animated);
    }

    public void SetCountdown(BubbleCountdown? countdown)
    {
        if (countdown == this.countdown)
            return;
        this.countdown = countdown;
        bubbleView.Countdown = countdown;
    }

    public void HideBubble(bool animated)
    {
        bubbleText = null;
        countdown = null;
        bubble.Hide(animated);
    }

    public void Dispose()
    {
        bubble.Dispose();
        character.Dispose();
        menuHost.Dispose();
    }

    // The views, for screenshot sheets

    /// <summary>The character standing on the desktop the way the companion's window draws it, over its contact shadow.</summary>
    public static FrameworkElement Stand(UIElement characterView, DesktopLayout layout) => new DesktopStand(characterView, layout, "", null);

    /// <summary>The desktop bubble the way the companion shows it; <paramref name="tail"/> is trailing when it sits left of the character.</summary>
    public static FrameworkElement BubblePreview(string text, BubbleTail tail, BubbleCountdown? countdown, DesktopLayout layout)
    {
        var view = new SpeechBubble
        {
            Text = text,
            Tail = tail,
            Countdown = countdown,
            FillsWidth = false,
            RoomForDial = true,
            FontSize = BubbleTextSize,
            MaxWidth = layout.BubbleMaxWidth,
        };
        var root = new Grid { Padding = new Thickness(layout.BubbleMargin) };
        root.Children.Add(view);
        return root;
    }

    // The bubble

    /// <summary>Beside the character's head, on the side with more room, the tail pointing at the character.</summary>
    private void PlaceBubble(bool measure)
    {
        if (bubbleText is null || !character.IsShown)
            return;
        var parent = ScreenRects.Rect(character.Frame);
        var work = ScreenRects.Rect(character.WorkArea);
        var side = FloatingPlacement.SideBeside(parent, work);
        bubbleView.Tail = side == FloatingPlacement.Side.Left ? BubbleTail.Trailing : BubbleTail.Leading;
        if (measure)
        {
            bubbleView.Text = bubbleText;
            bubbleView.Countdown = countdown;
            bubbleView.MaxWidth = layout.BubbleMaxWidth;
            bubbleRoot.Measure(new XamlSize(layout.BubbleMaxWidth + 2 * layout.BubbleMargin, double.PositiveInfinity));
            bubbleSize = new FigureSize(Math.Ceiling(bubbleRoot.DesiredSize.Width), Math.Ceiling(bubbleRoot.DesiredSize.Height));
        }
        bubble.SetFrame(ScreenRects.Int(BubbleFrame(layout, bubbleSize, parent, work, character.Scale)));
    }

    /// <summary>
    /// Where the bubble's window goes (screen pixels): beside <paramref name="parent"/> on the side with more room in
    /// <paramref name="work"/>, tucked in by the layout, the tail's tip level with the bubble pivot (the head's middle).
    /// <paramref name="bubble"/> is the measured bubble in DIPs, as the layout's numbers are; <paramref name="scale"/> is
    /// the display's.
    /// </summary>
    public static FigureRect BubbleFrame(DesktopLayout layout, FigureSize bubble, FigureRect parent, FigureRect work, double scale)
    {
        var side = FloatingPlacement.SideBeside(parent, work);
        var pixels = new FigureSize(Math.Ceiling(bubble.Width * scale), Math.Ceiling(bubble.Height * scale));
        var headY = parent.Origin.Y + (parent.Size.Height - layout.ShadowHeight * scale) * layout.HeadHeightRatio;
        // y down: the tip sits BubbleTailY below the bubble's top (the Mac, y up, adds where this subtracts).
        var centerY = headY - layout.BubbleTailY(bubble.Height) * scale + pixels.Height / 2;
        return FloatingPlacement.FrameBeside(pixels, parent, side, centerY, layout.BubbleGap(parent.Size.Width), work);
    }

    // The menu

    /// <summary>The menu at <paramref name="at"/> on screen, the mouse pointer's or the finger's: false when there is none.</summary>
    private bool ShowMenu(PointInt32 at)
    {
        if (Menu?.Invoke() is not { Count: > 0 } items)
            return false;
        menuHost.SetMenu(MenuConversion.ToTray(items));
        var before = Win32.GetForegroundWindow();
        menuHost.ShowMenu(at);
        // The menu took the foreground for its hidden window. Give it back after the chosen command (queued after the
        // menu) has run, unless that command opened a window of its own.
        queue.TryEnqueue(
            DispatcherQueuePriority.Low,
            () =>
            {
                var now = Win32.GetForegroundWindow();
                if (before != 0 && now != before && !ShellNative.IsWindowVisible(now))
                    Win32.SetForegroundWindow(before);
            }
        );
        return true;
    }

    // Position

    private void DragEnded(PointInt32 anchor)
    {
        PlaceBubble(measure: false);
        var moved = new DesktopPosition(anchor.X, anchor.Y);
        position = moved;
        OnMoved?.Invoke(moved);
    }

    /// <summary>The saved spot, or the bottom right of the primary display's work area.</summary>
    private static PointInt32 Anchor(DesktopPosition? position, FigureSize windowSize)
    {
        if (position is { } p)
            return new PointInt32(p.X, p.Y);
        var work = DisplayArea.Primary.WorkArea;
        var scale = ShellNative.Scale(work.X + work.Width / 2, work.Y + work.Height / 2);
        var pixels = new FigureSize(windowSize.Width * scale, windowSize.Height * scale);
        var anchor = FloatingPlacement.DefaultAnchor(pixels, ScreenRects.Rect(work), CornerInset * scale);
        return new PointInt32((int)Math.Round(anchor.X), (int)Math.Round(anchor.Y));
    }
}

/// <summary>
/// The character standing on the desktop over a soft contact shadow, so it looks like it stands rather than floats
/// (Swift's <c>CompanionStand</c> and <c>DesktopStand</c>). Narrator finds one button named like the character, whose
/// action says something.
/// </summary>
internal sealed partial class DesktopStand : Grid
{
    private readonly UIElement characterView;
    private readonly Ellipse shadow = new() { VerticalAlignment = VerticalAlignment.Bottom, IsHitTestVisible = false };
    private readonly RadialGradientBrush shade = new();
    private readonly string sayActionName;
    private readonly Action? say;

    public DesktopStand(UIElement characterView, DesktopLayout layout, string sayActionName, Action? say)
    {
        this.characterView = characterView;
        this.sayActionName = sayActionName;
        this.say = say;
        shadow.Height = layout.ShadowHeight * 2;
        shadow.Fill = shade;
        Children.Add(shadow);
        var holder = new Grid { Margin = new Thickness(0, 0, 0, layout.ShadowHeight) };
        holder.Children.Add(characterView);
        Children.Add(holder);
        SizeChanged += (_, e) => shadow.Width = e.NewSize.Width * layout.ShadowWidthRatio;
        LookBinding.Keep(this, Repaint);
    }

    /// <summary>Whether the character is drawn at <paramref name="point"/> (the window's content, DIPs): the canvases' own hit test, else XAML's.</summary>
    public bool IsDrawnAt(XamlPoint point)
    {
        var canvases = Canvases(characterView).ToArray();
        if (canvases.Length > 0)
            return canvases.Any(c => c.IsDrawnAt(point));
        return XamlRoot is not null && VisualTreeHelper.FindElementsInHostCoordinates(point, characterView).Any();
    }

    /// <summary>
    /// Boxes (the window's content, DIPs) that hold everything the stand draws: the canvases' parts, or the whole view when
    /// it has no canvas, and the contact shadow.
    /// </summary>
    public IReadOnlyList<FigureRect> DrawnBounds()
    {
        if (XamlRoot is null)
            return [];
        var canvases = Canvases(characterView).ToArray();
        List<FigureRect> boxes = canvases.Length > 0 ? [.. canvases.SelectMany(c => c.DrawnBounds())] : [Box(characterView)];
        boxes.Add(Box(shadow));
        return boxes;
    }

    private static FigureRect Box(UIElement element)
    {
        var box = element
            .TransformToVisual(null)
            .TransformBounds(new global::Windows.Foundation.Rect(0, 0, element.ActualSize.X, element.ActualSize.Y));
        return new FigureRect(box.X, box.Y, box.Width, box.Height);
    }

    /// <summary>The character's name, as its canvas tells Narrator (its line, or the app's label).</summary>
    public string AccessibleName =>
        Canvases(characterView).Select(AutomationProperties.GetName).FirstOrDefault(n => !string.IsNullOrEmpty(n)) ?? sayActionName;

    public string SayActionName => sayActionName;

    public void Say() => say?.Invoke();

    protected override AutomationPeer OnCreateAutomationPeer() => new StandPeer(this);

    private static IEnumerable<CharacterCanvas> Canvases(DependencyObject element)
    {
        if (element is CharacterCanvas canvas)
        {
            yield return canvas;
            yield break;
        }
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(element); i++)
            foreach (var found in Canvases(VisualTreeHelper.GetChild(element, i)))
                yield return found;
    }

    /// <summary>A soft ellipse: the palette's shade in the middle, nothing at the edge (the Mac blurs a filled one).</summary>
    private void Repaint()
    {
        var color = CompanionLook.Current.PaletteNow.Shade.For(ActualTheme);
        shade.GradientStops.Clear();
        shade.GradientStops.Add(new GradientStop { Color = color, Offset = 0 });
        shade.GradientStops.Add(new GradientStop { Color = color, Offset = 0.5 });
        shade.GradientStops.Add(new GradientStop { Color = global::Windows.UI.Color.FromArgb(0, color.R, color.G, color.B), Offset = 1 });
    }

    private sealed partial class StandPeer(DesktopStand owner) : FrameworkElementAutomationPeer(owner), IInvokeProvider
    {
        protected override AutomationControlType GetAutomationControlTypeCore() => AutomationControlType.Button;

        protected override string GetClassNameCore() => "DesktopCompanion";

        protected override string GetNameCore() => owner.AccessibleName;

        protected override string GetHelpTextCore() => owner.SayActionName;

        protected override IList<AutomationPeer> GetChildrenCore() => [];

        protected override object? GetPatternCore(PatternInterface patternInterface) =>
            patternInterface == PatternInterface.Invoke ? this : base.GetPatternCore(patternInterface);

        public void Invoke() => owner.Say();
    }
}
