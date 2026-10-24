using Laching.Companion.Shell.App;
using Laching.Windows.Companion.Views;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace Laching.Windows.Companion.Shell;

/// <summary>
/// The app's windows by id (<see cref="IWindowHost{TWindow}"/>, Swift's <c>WindowManager</c>), so they can be opened
/// from anywhere (the tray, a notification). The app supplies each window's <see cref="WindowSpec"/> and
/// content. A window's content reaches under its title bar (the Mac's transparent title bar): the window's paper fills
/// it, and the view sits under a band kept for the caption buttons (<see cref="WindowFrame"/>), so scrolled content
/// never slides under them; it is sized in DIPs on the display it opens on. The app has no taskbar button while
/// idle; each open window has its own. UI thread only.
/// </summary>
public sealed class WindowManager<TWindow> : IWindowHost<TWindow>
    where TWindow : notnull
{
    private readonly Laching.Windows.Shell.WindowManager<TWindow, EntryWindow> windows;
    private readonly Func<TWindow, WindowSpec> spec;
    private readonly Func<TWindow, UIElement> content;
    private readonly string? iconPath;
    private ElementTheme theme = ElementTheme.Default;

    /// <param name="spec">Each window's title, size and traits.</param>
    /// <param name="content">Each window's view, made when it opens; disposed when it closes if it is <see cref="IDisposable"/>.</param>
    /// <param name="iconPath">The <c>.ico</c> for the title bar and taskbar button; the executable's icon by default.</param>
    public WindowManager(Func<TWindow, WindowSpec> spec, Func<TWindow, UIElement> content, string? iconPath = null)
    {
        this.spec = spec;
        this.content = content;
        this.iconPath = iconPath;
        windows = new(Make, window => window.View as IDisposable, id => this.spec(id).IsExclusive, BringToFront);
        windows.Closed = id => OnClose?.Invoke(id);
    }

    public Action<TWindow>? OnClose { get; set; }

    /// <summary>
    /// Screenshot checks: windows open cloaked (drawn, but never on screen), out of the taskbar and Alt+Tab, behind the
    /// others and without taking the focus, so a run doesn't take the PC from whoever is using it. They lay out and are
    /// captured as usual, but inactive (grey caption buttons), so for layout checks and quick looks, not for the
    /// committed shots.
    /// </summary>
    public bool OpensHidden { get; set; }

    /// <summary>Light, dark or Windows' app mode (<see cref="ElementTheme.Default"/>), for every open window and new ones.</summary>
    public ElementTheme Theme
    {
        get => theme;
        set
        {
            theme = value;
            foreach (var entry in windows.Snapshot())
                ApplyTheme(entry);
        }
    }

    public bool IsOpen(TWindow id) => windows.Find(id) is not null;

    /// <summary>The open window, for the odd call this class doesn't cover.</summary>
    public Window? Find(TWindow id) => windows.Find(id);

    public bool ShowOpenWindows() => windows.ShowOpenWindows();

    public void Show(TWindow id) => windows.Show(id);

    public void Close(TWindow id) => windows.Close(id);

    public void CloseAll() => windows.CloseAll();

    private EntryWindow Make(TWindow id)
    {
        var spec = this.spec(id);
        var view = content(id);
        var root = WindowFrame.Make(view);
        var presenter = OverlappedPresenter.Create();
        presenter.IsMaximizable = false;
        presenter.IsAlwaysOnTop = spec.IsAlwaysOnTop;
        var entry = new EntryWindow(presenter, spec, root, view)
        {
            Title = spec.Title,
            Content = root,
            ExtendsContentIntoTitleBar = true,
        };
        entry.AppWindow.SetPresenter(presenter);
        if (iconPath is not null)
            entry.AppWindow.SetIcon(iconPath);
        ApplyTheme(entry);
        Place(entry);
        // Moving to a display with another scale keeps the content's size in DIPs; the minimum follows it.
        void Loaded(object sender, RoutedEventArgs args)
        {
            root.Loaded -= Loaded;
            root.XamlRoot.Changed += (_, _) => ApplyMinSize(entry);
        }
        root.Loaded += Loaded;
        return entry;
    }

    /// <summary>
    /// Sizes the content at <see cref="WindowSpec.Size"/> on the display it opens on (never past the work area, where it
    /// scrolls instead) and puts it in the middle or by the tray.
    /// </summary>
    private void Place(EntryWindow entry)
    {
        var (cursorX, cursorY) = NativeMethods.Cursor();
        var display =
            entry.Spec.Placement == WindowPlacement.NearTray
                ? DisplayArea.Primary
                : DisplayArea.GetFromPoint(new PointInt32(cursorX, cursorY), DisplayAreaFallback.Primary);
        var area = display.WorkArea;
        var scale = NativeMethods.Scale(area.X + area.Width / 2, area.Y + area.Height / 2);
        var appWindow = entry.AppWindow;
        // On the display first, so the client size below is measured with its frame.
        appWindow.Move(new PointInt32(area.X, area.Y));
        NativeMethods.SetClientSize(
            appWindow,
            new SizeInt32(
                Math.Min((int)Math.Round(entry.Spec.Size.Width * scale), area.Width),
                Math.Min((int)Math.Round(entry.Spec.Size.Height * scale), area.Height)
            )
        );
        var size = appWindow.Size;
        var origin =
            entry.Spec.Placement == WindowPlacement.NearTray
                ? TrayPlacement.Corner(area, display.OuterBounds, size, (int)Math.Round(12 * scale))
                : new PointInt32(area.X + (area.Width - size.Width) / 2, area.Y + (area.Height - size.Height) / 2);
        appWindow.Move(origin);
        ApplyMinSize(entry, scale);
    }

    private static void ApplyMinSize(EntryWindow entry, double? scale = null)
    {
        if (entry.Spec.MinSize is not { } min)
            return;
        var factor = scale ?? entry.Root.XamlRoot?.RasterizationScale ?? 1;
        entry.Presenter.PreferredMinimumWidth = (int)Math.Round(min.Width * factor);
        entry.Presenter.PreferredMinimumHeight = (int)Math.Round(min.Height * factor);
    }

    private void ApplyTheme(EntryWindow entry)
    {
        entry.Root.RequestedTheme = theme;
        entry.AppWindow.TitleBar.PreferredTheme = theme switch
        {
            ElementTheme.Light => TitleBarTheme.Light,
            ElementTheme.Dark => TitleBarTheme.Dark,
            _ => TitleBarTheme.UseDefaultAppMode,
        };
    }

    private void BringToFront(EntryWindow entry)
    {
        if (OpensHidden)
        {
            NativeMethods.ShowHidden(entry.AppWindow);
            return;
        }
        Laching.Windows.Shell.WindowManager<TWindow, EntryWindow>.BringToFront(entry);
    }

    private sealed partial class EntryWindow(OverlappedPresenter presenter, WindowSpec spec, Grid root, UIElement view) : Window
    {
        public OverlappedPresenter Presenter { get; } = presenter;
        public WindowSpec Spec { get; } = spec;
        public Grid Root { get; } = root;
        public UIElement View { get; } = view;
    }
}
