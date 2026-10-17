using System.Diagnostics;
using Laching.Companion.Shell.Desktop;
using Laching.Windows.Companion.Views;
using Microsoft.UI;
using Microsoft.UI.Composition;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;
using FigurePoint = Laching.Companion.Figures.Point;
using FigureRect = Laching.Companion.Figures.Rect;
using FigureSize = Laching.Companion.Figures.Size;
using ShellNative = Laching.Windows.Companion.Shell.NativeMethods;
using Win32 = Laching.Windows.Shell.NativeMethods;
using XamlPoint = Windows.Foundation.Point;

namespace Laching.Windows.Companion.Desktop;

/// <summary>
/// A small window floating above other apps' windows, for a desk character and its speech bubble (Swift's
/// <c>FloatingWindow</c>), as 3.1's spike found it: a WinUI window with a fully transparent backdrop, layered, topmost,
/// never activated, in neither the taskbar nor Alt+Tab, so the app in front keeps the keyboard. Mouse clicks pass
/// through everywhere but where something is drawn (<see cref="HitTest"/>): a poll off the UI thread watches the mouse
/// pointer and, while it is on the window, turns the window's click-through off only over a drawn pixel. While the mouse
/// pointer is off the window, the window takes input, for a finger or a pen: Windows routes a touch before the app sees
/// it, so a click-through window never gets one. Then a window region keeps it to what is drawn (<see cref="DrawnBounds"/>,
/// grown and held by <see cref="TouchArea"/>), or the whole window without them. A region also clips the drawing: a pose
/// that moves past it between two updates (100 ms apart) is cut until the next.
/// </summary>
/// <remarks>
/// It stands on its <see cref="Anchor"/>, the middle of its bottom edge in screen pixels (y down), so
/// <see cref="SetSize"/> grows it upward in place; its size is in DIPs, at the scale of the display it stands on. It stays
/// wholly on a display's work area (<see cref="FloatingPlacement.Clamped"/>): a drag ends inside one, and when displays
/// change it moves onto one, returning to the anchor once that fits again. A press goes by its own pointer's positions,
/// the mouse's or a finger's (<see cref="PressTracker"/>): a click (under <see cref="DragThreshold"/> DIPs of movement for
/// the mouse, <see cref="TouchDragThreshold"/> for a finger or a pen) calls <see cref="OnClick"/>; a one-finger or mouse
/// drag moves it and reports the new anchor; a right click, or a finger or a pen held as still for
/// <see cref="HoldGesture.HoldSeconds"/> and lifted, calls <see cref="OnMenuAt"/> (or <see cref="OnMenu"/>) at the
/// pointer; Ctrl+wheel (which a precision touchpad's pinch sends) and a touch pinch report one step per gesture, and a
/// pinch is never a click, a drag or a hold. A second window can ride along (<see cref="AttachTo"/>, for the bubble):
/// Windows keeps it above its owner, and the app places it with <see cref="SetFrame"/>. UI thread only.
/// </remarks>
public sealed class DesktopWindow : IDisposable
{
    public const double FadeInSeconds = 0.4;
    public const double FadeOutSeconds = 0.2;

    /// <summary>How far the mouse pointer moves, in DIPs, before a press is a drag rather than a click.</summary>
    public const double DragThreshold = 3;

    /// <summary>
    /// The same for a finger or a pen, which drifts more while it rests: Windows' own threshold between a tap and a drag,
    /// 2.7 mm (about 10 DIPs). A press-and-hold has to stay within it too.
    /// </summary>
    public const double TouchDragThreshold = 10;

    /// <summary>The wheel turns one notch (120) per step; a touch pinch must scale this much.</summary>
    private const int WheelStep = 120;
    private const double PinchStep = 0.15;

    /// <summary>How long the wheel rests before a Ctrl+wheel gesture is over.</summary>
    private const double WheelRestSeconds = 0.4;

    /// <summary>
    /// How often the pointer is looked at for click-through, off the UI thread: often while it is near the window, so
    /// the window takes a click the moment the pointer reaches the character, and less often while it is far.
    /// </summary>
    private const int PollNearMilliseconds = 33;
    private const int PollFarMilliseconds = 100;

    /// <summary>How far from the window, in pixels, the pointer counts as near.</summary>
    private const int NearPixels = 160;

    private readonly Window window;
    private readonly Grid root = new() { Background = new SolidColorBrush(Colors.Transparent) };
    private readonly DispatcherQueue queue;
    private readonly Timer poll;
    private readonly DisplayAreaWatcher displays;
    private DesktopWindow? parent;
    private PointInt32 anchor;
    private FigureSize size;
    private double placedScale;
    private int generation;
    private bool displaysEnumerated;

    // Set on the UI thread, read by the poll.
    private volatile bool polling;
    private volatile bool pressed;
    private volatile bool clickThrough;
    private volatile bool hitPending;

    private readonly PressTracker press = new();
    private RectInt32 frameAtPress;

    /// <summary>Where a finger reaches while the mouse pointer is off the window, and the region set from it (none: empty).</summary>
    private readonly TouchArea touchArea = new();
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private IReadOnlyList<RectInt32> region = [];
    private int wheel;
    private bool wheelStepped;
    private IDisposable? wheelEnd;
    private bool pinched;

    /// <summary>Times a finger's or a pen's press for a hold; the press's end stops it.</summary>
    private IDisposable? holdTimer;

    private readonly Shell.DispatcherTimers timers;

    /// <summary>
    /// Whether the last press was a finger's or a pen's that came up a click or a hold, which XAML may follow with a
    /// RightTapped of its own.
    /// </summary>
    private bool touchAnswered;

    /// <param name="content">What the window shows; it fills the window.</param>
    /// <param name="size">The window's size in DIPs.</param>
    /// <param name="anchor">The middle of its bottom edge, in screen pixels.</param>
    /// <param name="isMovable">Whether a drag moves it; off for a window riding along beside another.</param>
    public DesktopWindow(UIElement content, FigureSize size, PointInt32 anchor, bool isMovable = true)
    {
        queue = Shell.UiThread.Resolve(queue: null, "desktop window");
        timers = new Shell.DispatcherTimers(queue);
        this.size = size;
        this.anchor = anchor;
        IsMovable = isMovable;
        root.Children.Add(content);
        window = new Window { Content = root, SystemBackdrop = new TransparentBackdrop() };
        // The context-menu presenter is the one whose popup style sticks: WS_POPUP and nothing else, so no frame.
        var presenter = OverlappedPresenter.CreateForContextMenu();
        presenter.SetBorderAndTitleBar(false, false);
        presenter.IsResizable = presenter.IsMaximizable = presenter.IsMinimizable = false;
        presenter.IsAlwaysOnTop = true;
        window.AppWindow.SetPresenter(presenter);
        window.AppWindow.IsShownInSwitchers = false;
        Hwnd = Win32Interop.GetWindowFromWindowId(window.AppWindow.Id);
        var ex = ShellNative.GetWindowLongPtr(Hwnd, ShellNative.GWL_EXSTYLE);
        ex |= (nint)(ShellNative.WS_EX_LAYERED | ShellNative.WS_EX_TOOLWINDOW | ShellNative.WS_EX_NOACTIVATE);
        ex &= ~(nint)ShellNative.WS_EX_APPWINDOW;
        ShellNative.SetWindowLongPtr(Hwnd, ShellNative.GWL_EXSTYLE, ex);
        // No Windows 11 rounded corners or accent border around a character.
        ShellNative.SquareCorners(Hwnd);
        SetClickThrough(true);

        root.PointerPressed += OnPressed;
        root.PointerMoved += OnMoved;
        root.PointerReleased += OnReleased;
        root.PointerCaptureLost += (_, e) => EndPress(e.Pointer.PointerId);
        root.PointerCanceled += (_, e) => EndPress(e.Pointer.PointerId);
        root.RightTapped += (_, e) =>
        {
            e.Handled = true;
            // A finger's or a pen's press that came up a hold or a click has had its answer already.
            if (touchAnswered && e.PointerDeviceType != Microsoft.UI.Input.PointerDeviceType.Mouse)
                return;
            ShowMenu(e.GetPosition(root));
        };
        root.PointerWheelChanged += OnWheel;
        root.ManipulationMode = ManipulationModes.Scale;
        root.ManipulationStarted += (_, _) => pinched = false;
        root.ManipulationDelta += OnPinchDelta;
        root.Loaded += (_, _) => root.XamlRoot.Changed += (_, _) => ScaleMaybeChanged();

        poll = new Timer(_ => Poll(), null, Timeout.Infinite, Timeout.Infinite);
        displays = DisplayArea.CreateWatcher();
        displays.EnumerationCompleted += (_, _) => queue.TryEnqueue(() => displaysEnumerated = true);
        displays.Added += (_, _) => queue.TryEnqueue(ScreensChanged);
        displays.Removed += (_, _) => queue.TryEnqueue(ScreensChanged);
        displays.Updated += (_, _) => queue.TryEnqueue(ScreensChanged);
        displays.Start();
        Place();
    }

    /// <summary>A click without a drag.</summary>
    public Action? OnClick { get; set; }

    /// <summary>The window moves under a drag (each step), for a window riding along to follow.</summary>
    public Action? OnDragging { get; set; }

    /// <summary>A drag ended; the new anchor, already on a display.</summary>
    public Action<PointInt32>? OnDragEnded { get; set; }

    /// <summary>A pinch or Ctrl+wheel: +1 bigger, -1 smaller, once per gesture.</summary>
    public Action<int>? OnPinch { get; set; }

    /// <summary>
    /// A right click or a touch or pen press-and-hold: show the menu. <see cref="OnMenuAt"/>, when set, is called instead.
    /// </summary>
    public Action? OnMenu { get; set; }

    /// <summary>
    /// A right click or a touch or pen press-and-hold: show the menu at this point on screen (pixels), where the mouse
    /// pointer or the finger is (a finger doesn't move the mouse pointer), and say whether one showed: a hold with no menu
    /// to show is a click (<see cref="OnClick"/>). Called instead of <see cref="OnMenu"/> when set.
    /// </summary>
    public Func<PointInt32, bool>? OnMenuAt { get; set; }

    /// <summary>The displays changed (one was added, removed or rearranged), after the window moved onto one.</summary>
    public Action? OnScreensChanged { get; set; }

    /// <summary>
    /// Whether something is drawn at a point in the content (DIPs): only there do clicks land on the window. Null takes
    /// any element XAML finds there.
    /// </summary>
    public Func<XamlPoint, bool>? HitTest { get; set; }

    /// <summary>
    /// Boxes in the content (DIPs) that hold everything drawn, a shadow included: while the mouse pointer is off the
    /// window, a finger or a pen reaches it only there (grown and held a few seconds, <see cref="TouchArea"/>). Null: the
    /// whole window, as for a bubble.
    /// </summary>
    public Func<IReadOnlyList<FigureRect>>? DrawnBounds { get; set; }

    public bool IsMovable { get; set; }

    public bool IsShown { get; private set; }

    public nint Hwnd { get; }

    /// <summary>The window's title, which Narrator reads for it; WinUI's until set. Never shown: the window has no title bar.</summary>
    public string Title
    {
        get => window.Title;
        set => window.Title = value;
    }

    /// <summary>The content's root: the window's background, for the app's theme.</summary>
    public FrameworkElement Root => root;

    /// <summary>Where the window stands (screen pixels). Setting it moves the window (onto a display).</summary>
    public PointInt32 Anchor
    {
        get => anchor;
        set
        {
            anchor = value;
            Place();
        }
    }

    /// <summary>The window's size in DIPs.</summary>
    public FigureSize Size => size;

    /// <summary>The window's frame on screen right now, in pixels.</summary>
    public RectInt32 Frame
    {
        get
        {
            var r = Pointer().Frame;
            return new RectInt32(r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top);
        }
    }

    /// <summary>The scale of the display the window is on (1 at 100 %).</summary>
    public double Scale => ShellNative.Scale(Hwnd);

    /// <summary>The work area (without the taskbar) of the display the window is on, in pixels.</summary>
    public RectInt32 WorkArea => DisplayArea.GetFromWindowId(window.AppWindow.Id, DisplayAreaFallback.Nearest).WorkArea;

    /// <summary>Every display's work area, in pixels.</summary>
    public static IReadOnlyList<FigureRect> WorkAreas()
    {
        // By index: enumerating the projected list throws InvalidCastException (a known Windows App SDK projection bug).
        var displays = DisplayArea.FindAll();
        var areas = new List<FigureRect>(displays.Count);
        for (var i = 0; i < displays.Count; i++)
            areas.Add(ScreenRects.Rect(displays[i].WorkArea));
        return areas;
    }

    /// <summary>Resizes around the anchor: the bottom middle stays put.</summary>
    public void SetSize(FigureSize size)
    {
        this.size = size;
        // The content scales with the window: the held boxes no longer hold it.
        touchArea.Clear();
        Unshape();
        Place();
    }

    /// <summary>Puts a riding-along window exactly at <paramref name="frame"/> (pixels), without clamping.</summary>
    public void SetFrame(RectInt32 frame)
    {
        var scale = ShellNative.Scale(frame.X + frame.Width / 2, frame.Y + frame.Height / 2);
        size = new FigureSize(frame.Width / scale, frame.Height / scale);
        window.AppWindow.MoveAndResize(frame);
    }

    /// <summary>Rides along with <paramref name="owner"/> from now on: Windows keeps it above, and the app places it.</summary>
    public void AttachTo(DesktopWindow owner)
    {
        parent = owner;
        ShellNative.SetWindowLongPtr(Hwnd, ShellNative.GWLP_HWNDPARENT, owner.Hwnd);
    }

    /// <summary>Shows the window without activating it, fading in unless <paramref name="animated"/> is false.</summary>
    public void Show(bool animated)
    {
        generation++;
        var wasVisible = window.AppWindow.IsVisible;
        IsShown = true;
        if (parent is null)
            Place();
        var visual = ElementCompositionPreview.GetElementVisual(root);
        visual.StopAnimation(nameof(Visual.Opacity));
        if (animated && !wasVisible)
        {
            visual.Opacity = 0;
            window.AppWindow.Show(activateWindow: false);
            Fade(visual, 1, FadeInSeconds, null);
        }
        else
        {
            visual.Opacity = 1;
            window.AppWindow.Show(activateWindow: false);
        }
        polling = true;
        poll.Change(0, Timeout.Infinite);
    }

    /// <summary>Fades the window out (unless <paramref name="animated"/> is false), then hides it.</summary>
    public void Hide(bool animated)
    {
        if (!IsShown)
            return;
        IsShown = false;
        generation++;
        var current = generation;
        polling = false;
        poll.Change(Timeout.Infinite, Timeout.Infinite);
        SetClickThrough(true);
        Unshape();
        var visual = ElementCompositionPreview.GetElementVisual(root);
        if (!animated || !window.AppWindow.IsVisible)
        {
            window.AppWindow.Hide();
            return;
        }
        Fade(
            visual,
            0,
            FadeOutSeconds,
            () =>
            {
                if (generation != current)
                    return;
                window.AppWindow.Hide();
                visual.Opacity = 1;
            }
        );
    }

    public void Dispose()
    {
        poll.Dispose();
        displays.Stop();
        wheelEnd?.Dispose();
        holdTimer?.Dispose();
        window.Close();
    }

    // Placement

    private void Place()
    {
        if (parent is not null)
            return;
        var scale = ShellNative.Scale(anchor.X, anchor.Y);
        placedScale = scale;
        var pixels = new FigureSize(Math.Round(size.Width * scale), Math.Round(size.Height * scale));
        var target = FloatingPlacement.Clamped(new FigurePoint(anchor.X, anchor.Y), pixels, WorkAreas());
        var frame = FloatingPlacement.Frame(target, pixels);
        window.AppWindow.MoveAndResize(ScreenRects.Int(frame));
    }

    private void ScaleMaybeChanged()
    {
        if (parent is null && Math.Abs(ShellNative.Scale(anchor.X, anchor.Y) - placedScale) > 0.001)
            Place();
    }

    private void ScreensChanged()
    {
        if (!displaysEnumerated)
            return;
        Place();
        OnScreensChanged?.Invoke();
    }

    // Click-through: the pointer is read off the UI thread; the UI thread wakes only while it is over the window.

    private void Poll()
    {
        var (point, rect, inside) = Pointer();
        var near =
            point.X >= rect.Left - NearPixels && point.X < rect.Right + NearPixels && point.Y >= rect.Top - NearPixels && point.Y < rect.Bottom + NearPixels;
        // One-shot, re-armed each time: never two polls at once, and none after a hide.
        if (polling)
            poll.Change(near || pressed ? PollNearMilliseconds : PollFarMilliseconds, Timeout.Infinite);
        // Away from the window the mouse can't click it, so it stays hit-testable there for a finger or a pen: Windows
        // routes a touch before the app sees it, and a click-through window never gets one. A shaped window wakes the
        // UI thread to follow the drawing with its region.
        if (!inside && !pressed && DrawnBounds is null)
        {
            if (clickThrough)
                queue.TryEnqueue(() => SetClickThrough(false));
            return;
        }
        if (hitPending)
            return;
        hitPending = true;
        queue.TryEnqueue(() =>
        {
            hitPending = false;
            Hit();
        });
    }

    private void Hit()
    {
        if (!IsShown)
            return;
        var (point, rect, inside) = Pointer();
        var scale = Scale;
        var local = new XamlPoint((point.X - rect.Left) / scale, (point.Y - rect.Top) / scale);
        if (inside)
            Unshape();
        else
            Shape();
        SetClickThrough(inside && !pressed && !IsDrawn(local));
    }

    /// <summary>The mouse pointer and the window's frame on screen now (pixels), and whether the pointer is on the window. Any thread.</summary>
    private (Win32.POINT Cursor, Win32.RECT Frame, bool Inside) Pointer()
    {
        Win32.GetCursorPos(out var cursor);
        Win32.GetWindowRect(Hwnd, out var frame);
        var inside = cursor.X >= frame.Left && cursor.X < frame.Right && cursor.Y >= frame.Top && cursor.Y < frame.Bottom;
        return (cursor, frame, inside);
    }

    /// <summary>Keeps the window to <see cref="DrawnBounds"/>, grown and held, as its region; unchanged boxes set nothing.</summary>
    private void Shape()
    {
        if (DrawnBounds is not { } drawn)
            return;
        var frame = Frame;
        var boxes = touchArea.Update(clock.Elapsed.TotalSeconds, drawn(), Scale, frame.Width, frame.Height);
        if (boxes.Count == 0)
        {
            Unshape();
            return;
        }
        if (boxes.SequenceEqual(region))
            return;
        var shape = ShellNative.CreateRectRgn(0, 0, 0, 0);
        foreach (var box in boxes)
        {
            var part = ShellNative.CreateRectRgn(box.X, box.Y, box.X + box.Width, box.Y + box.Height);
            ShellNative.CombineRgn(shape, shape, part, ShellNative.RGN_OR);
            ShellNative.DeleteObject(part);
        }
        if (ShellNative.SetWindowRgn(Hwnd, shape, true) == 0)
        {
            ShellNative.DeleteObject(shape);
            return;
        }
        region = boxes;
    }

    /// <summary>The whole window again: the mouse pointer is on it, or the drawing changed size.</summary>
    private void Unshape()
    {
        if (region.Count == 0)
            return;
        ShellNative.SetWindowRgn(Hwnd, 0, true);
        region = [];
    }

    private bool IsDrawn(XamlPoint point)
    {
        if (HitTest is { } test)
            return test(point);
        if (root.XamlRoot is null)
            return false;
        return VisualTreeHelper.FindElementsInHostCoordinates(point, root).Any(e => !ReferenceEquals(e, root));
    }

    private void SetClickThrough(bool on)
    {
        if (on == clickThrough && on == HasTransparentStyle())
            return;
        clickThrough = on;
        var ex = ShellNative.GetWindowLongPtr(Hwnd, ShellNative.GWL_EXSTYLE);
        ex = on ? ex | (nint)ShellNative.WS_EX_TRANSPARENT : ex & ~(nint)ShellNative.WS_EX_TRANSPARENT;
        ShellNative.SetWindowLongPtr(Hwnd, ShellNative.GWL_EXSTYLE, ex);
    }

    private bool HasTransparentStyle() => (ShellNative.GetWindowLongPtr(Hwnd, ShellNative.GWL_EXSTYLE) & (nint)ShellNative.WS_EX_TRANSPARENT) != 0;

    // Pointer: clicks, drags, the menu, pinches

    private void OnPressed(object sender, PointerRoutedEventArgs e)
    {
        touchAnswered = false;
        var point = e.GetCurrentPoint(root);
        var mouse = e.Pointer.PointerDeviceType == Microsoft.UI.Input.PointerDeviceType.Mouse;
        if (point.Properties.IsRightButtonPressed || (mouse && !point.Properties.IsLeftButtonPressed))
            return;
        root.CapturePointer(e.Pointer);
        var at = OnScreen(point.Position);
        switch (press.Down(e.Pointer.PointerId, at.X, at.Y))
        {
            case PressTracker.Start.Press:
                frameAtPress = Frame;
                // A finger or a pen held still is a right click, as across Windows; XAML raises no RightTapped for one
                // on this window. The mouse has its right button.
                holdTimer?.Dispose();
                holdTimer = !mouse && (OnMenu is not null || OnMenuAt is not null) ? timers.After(HoldGesture.HoldSeconds, press.Hold) : null;
                break;
            case PressTracker.Start.EndsDrag:
                DragEnded();
                break;
        }
        pressed = press.IsPressed;
    }

    private void OnMoved(object sender, PointerRoutedEventArgs e)
    {
        if (!IsMovable || !press.IsPressed)
            return;
        var at = OnScreen(e.GetCurrentPoint(root).Position);
        var threshold = DragThresholdFor(e.Pointer.PointerDeviceType == Microsoft.UI.Input.PointerDeviceType.Mouse) * Scale;
        if (press.Move(e.Pointer.PointerId, at.X, at.Y, threshold) is not var (dx, dy))
            return;
        window.AppWindow.Move(new PointInt32(frameAtPress.X + dx, frameAtPress.Y + dy));
        OnDragging?.Invoke();
    }

    private void OnReleased(object sender, PointerRoutedEventArgs e)
    {
        // Read first: releasing the capture raises PointerCaptureLost at once, which ends the press.
        var end = press.Up(e.Pointer.PointerId);
        var at = e.GetCurrentPoint(root).Position;
        pressed = press.IsPressed;
        if (!pressed)
            holdTimer?.Dispose();
        root.ReleasePointerCapture(e.Pointer);
        if ((end is PressTracker.End.Click or PressTracker.End.Hold) && e.Pointer.PointerDeviceType != Microsoft.UI.Input.PointerDeviceType.Mouse)
            touchAnswered = true;
        if (end != PressTracker.End.Hold)
            Ended(end);
        else if (!ShowMenu(at))
            OnClick?.Invoke();
    }

    /// <summary>The capture went elsewhere mid-press: a drag ends where it is, a click or a hold doesn't happen.</summary>
    private void EndPress(uint pointer)
    {
        var end = press.Lost(pointer);
        pressed = press.IsPressed;
        if (!pressed)
            holdTimer?.Dispose();
        Ended(end);
    }

    /// <summary>The menu, for a right click or a hold at <paramref name="local"/> in the content (DIPs): whether one showed.</summary>
    private bool ShowMenu(XamlPoint local)
    {
        if (OnMenuAt is { } menuAt)
        {
            var at = OnScreen(local);
            return menuAt(new PointInt32((int)Math.Round(at.X), (int)Math.Round(at.Y)));
        }
        if (OnMenu is not { } menu)
            return false;
        menu();
        return true;
    }

    /// <summary>How far a press's pointer moves, in DIPs, before the press is a drag: a finger or a pen goes further.</summary>
    internal static double DragThresholdFor(bool mouse) => mouse ? DragThreshold : TouchDragThreshold;

    private void Ended(PressTracker.End end)
    {
        if (end == PressTracker.End.Drag)
            DragEnded();
        else if (end == PressTracker.End.Click)
            OnClick?.Invoke();
    }

    /// <summary>A point in the content (DIPs) on screen (pixels), from where the window is now.</summary>
    private XamlPoint OnScreen(XamlPoint local)
    {
        var frame = Frame;
        var scale = Scale;
        return new XamlPoint(frame.X + local.X * scale, frame.Y + local.Y * scale);
    }

    private void DragEnded()
    {
        var dropped = FloatingPlacement.Anchor(ScreenRects.Rect(Frame));
        var scale = ShellNative.Scale((int)dropped.X, (int)dropped.Y);
        var pixels = new FigureSize(Math.Round(size.Width * scale), Math.Round(size.Height * scale));
        var clamped = FloatingPlacement.Clamped(dropped, pixels, WorkAreas());
        anchor = new PointInt32((int)Math.Round(clamped.X), (int)Math.Round(clamped.Y));
        Place();
        OnDragEnded?.Invoke(anchor);
    }

    private void OnWheel(object sender, PointerRoutedEventArgs e)
    {
        if ((e.KeyModifiers & global::Windows.System.VirtualKeyModifiers.Control) == 0)
            return;
        e.Handled = true;
        wheel += e.GetCurrentPoint(root).Properties.MouseWheelDelta;
        if (!wheelStepped && Math.Abs(wheel) >= WheelStep)
        {
            wheelStepped = true;
            OnPinch?.Invoke(wheel > 0 ? 1 : -1);
        }
        // A gesture ends when the wheel rests for a moment.
        wheelEnd?.Dispose();
        wheelEnd = timers.After(WheelRestSeconds, () => (wheel, wheelStepped) = (0, false));
    }

    private void OnPinchDelta(object sender, ManipulationDeltaRoutedEventArgs e)
    {
        var scale = e.Cumulative.Scale;
        if (scale != 1 && press.Pinch() == PressTracker.End.Drag)
            DragEnded();
        if (pinched || Math.Abs(scale - 1) < PinchStep)
            return;
        pinched = true;
        OnPinch?.Invoke(scale > 1 ? 1 : -1);
    }

    private static void Fade(Visual visual, float to, double seconds, Action? completed)
    {
        var compositor = visual.Compositor;
        var batch = compositor.CreateScopedBatch(CompositionBatchTypes.Animation);
        var fade = compositor.CreateScalarKeyFrameAnimation();
        fade.InsertKeyFrame(1, to);
        fade.Duration = TimeSpan.FromSeconds(seconds);
        visual.StartAnimation(nameof(Visual.Opacity), fade);
        batch.End();
        if (completed is not null)
            batch.Completed += (_, _) => completed();
    }
}
