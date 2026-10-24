namespace Laching.Windows.Shell;

// Weak listeners: closing a WinUI window need not raise Unloaded. Its elements must still be collectable.
internal sealed class ChangeListeners
{
    private readonly List<WeakReference<Action>> listeners = [];

    public void Add(Action listener)
    {
        Remove(listener);
        listeners.Add(new(listener));
    }

    public void Remove(Action listener) => listeners.RemoveAll(reference => !reference.TryGetTarget(out var target) || target == listener);

    public void Raise()
    {
        foreach (var reference in listeners.ToArray())
            if (reference.TryGetTarget(out var listener) && listeners.Contains(reference))
                listener();
        listeners.RemoveAll(reference => !reference.TryGetTarget(out _));
    }
}

// Each element owns a distinct subscription, even when two elements share the same repaint delegate.
internal sealed class ChangeBinding(ChangeListeners listeners, Action apply)
{
    private readonly Action repaint = () => apply();
    public void Apply() => repaint();
    public void Loaded()
    {
        listeners.Add(repaint);
        repaint();
    }
    public void Unloaded() => listeners.Remove(repaint);
}
