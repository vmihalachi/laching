using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;

namespace Laching.Windows.Shell;

/// <summary>
/// Owns a tray app's open windows and their content, independently of the companion shell. The app creates each
/// window and supplies its exclusivity policy; this class tracks activation, reopens existing windows and disposes
/// their owned content when Closed fires (WinUI does not reliably raise Unloaded then). UI thread only.
/// </summary>
public sealed class WindowManager<TId, TWindow>
    where TId : notnull
    where TWindow : Window
{
    private readonly WindowLifetime<TId, TWindow> lifetime;

    /// <param name="create">Creates and configures a new window. It must not activate or close it before returning.</param>
    /// <param name="content">The content owned by each window, disposed once when it closes; null if it owns none.</param>
    /// <param name="exclusive">Whether an id shares a single slot with the other exclusive ids.</param>
    /// <param name="activate">Optional custom activation (for example hidden screenshot windows); restores and foregrounds by default.</param>
    public WindowManager(
        Func<TId, TWindow> create,
        Func<TWindow, IDisposable?> content,
        Func<TId, bool>? exclusive = null,
        Action<TWindow>? activate = null
    )
    {
        lifetime = new(
            id => Attach(id, create(id)),
            exclusive ?? (_ => false),
            window => window.Close(),
            activate ?? BringToFront,
            content
        );
    }

    private TWindow Attach(TId id, TWindow window)
    {
        window.Activated += (_, args) => lifetime.DidActivate(id, window, args.WindowActivationState != WindowActivationState.Deactivated);
        window.Closed += (_, _) => lifetime.DidClose(id, window);
        return window;
    }

    /// <summary>A window closed, after removal and content disposal; reopening the same id here is safe.</summary>
    public Action<TId>? Closed { get => lifetime.Closed; set => lifetime.Closed = value; }

    /// <summary>An owned window became active.</summary>
    public Action? Activated { get => lifetime.Activated; set => lifetime.Activated = value; }

    /// <summary>The number of open windows.</summary>
    public int Count => lifetime.Count;

    /// <summary>Whether one of this manager's windows is active.</summary>
    public bool HasActiveWindow => lifetime.Active is not null;

    /// <summary>The open window, or null.</summary>
    public TWindow? Find(TId id) => lifetime.Find(id);

    /// <summary>A snapshot of open windows, safe to iterate while app callbacks open or close windows.</summary>
    public TWindow[] Snapshot() => lifetime.Snapshot();

    /// <summary>Opens or foregrounds a window, closing other exclusive windows if its policy requires it.</summary>
    public TWindow Show(TId id) => lifetime.Show(id);

    /// <summary>Opens using a factory specific to this request (such as a camera session's size); an existing window is reused.</summary>
    public TWindow Show(TId id, Func<TWindow> create) => lifetime.Show(id, () => Attach(id, create()));

    /// <summary>Foregrounds the open windows; false when there were none.</summary>
    public bool ShowOpenWindows() => lifetime.ShowOpenWindows();

    /// <summary>Closes an open window.</summary>
    public void Close(TId id) => lifetime.Close(id);

    /// <summary>Closes the current open windows; windows opened by close callbacks belong to the next operation.</summary>
    public void CloseAll() => lifetime.CloseAll();

    /// <summary>Restores a minimized window and brings it forward, including when invoked by a tray menu.</summary>
    public static void BringToFront(TWindow window)
    {
        if (XamlCast.As<OverlappedPresenter>(window.AppWindow.Presenter) is { State: OverlappedPresenterState.Minimized } presenter)
            presenter.Restore();
        window.Activate();
        NativeMethods.SetForegroundWindow(WinRT.Interop.WindowNative.GetWindowHandle(window));
    }
}
