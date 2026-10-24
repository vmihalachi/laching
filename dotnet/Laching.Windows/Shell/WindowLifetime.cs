namespace Laching.Windows.Shell;

// UI-independent bookkeeping, also compiled by the portable tests. The WinUI adapter supplies the OS operations.
internal sealed class WindowLifetime<TId, TWindow>(
    Func<TId, TWindow> create,
    Func<TId, bool> exclusive,
    Action<TWindow> close,
    Action<TWindow> activate,
    Func<TWindow, IDisposable?> content
)
    where TId : notnull
    where TWindow : class
{
    private readonly Dictionary<TId, TWindow> windows = [];
    public Action<TId>? Closed { get; set; }
    public Action? Activated { get; set; }
    public TWindow? Active { get; private set; }
    public int Count => windows.Count;
    public TWindow? Find(TId id) => windows.GetValueOrDefault(id);
    public TWindow[] Snapshot() => windows.Values.ToArray();

    public TWindow Show(TId id, Func<TWindow>? make = null)
    {
        if (!windows.TryGetValue(id, out var window))
        {
            window = make is null ? create(id) : make();
            windows.Add(id, window);
        }
        if (exclusive(id))
            foreach (var (other, candidate) in windows.ToArray())
                if (!EqualityComparer<TId>.Default.Equals(other, id) && exclusive(other) && IsCurrent(other, candidate))
                    close(candidate);
        // Closing another window invokes app code, which may have closed or replaced this one as well.
        if (IsCurrent(id, window))
            activate(window);
        return window;
    }

    public bool ShowOpenWindows()
    {
        var snapshot = windows.ToArray();
        foreach (var (id, window) in snapshot)
            if (IsCurrent(id, window))
                activate(window);
        return snapshot.Length > 0;
    }

    public void Close(TId id)
    {
        if (Find(id) is { } window)
            close(window);
    }

    public void CloseAll()
    {
        foreach (var (id, window) in windows.ToArray())
            if (IsCurrent(id, window))
                close(window);
    }

    public void DidActivate(TId id, TWindow window, bool active)
    {
        if (!IsCurrent(id, window))
            return;
        if (active)
        {
            Active = window;
            Activated?.Invoke();
        }
        else if (ReferenceEquals(Active, window))
            Active = null;
    }

    public void DidClose(TId id, TWindow window)
    {
        if (!IsCurrent(id, window))
            return;
        windows.Remove(id);
        if (ReferenceEquals(Active, window))
            Active = null;
        // Remove first: disposal and the app callback may synchronously reopen the same id.
        try
        {
            content(window)?.Dispose();
        }
        finally
        {
            Closed?.Invoke(id);
        }
    }

    private bool IsCurrent(TId id, TWindow window) => windows.TryGetValue(id, out var current) && ReferenceEquals(current, window);
}
