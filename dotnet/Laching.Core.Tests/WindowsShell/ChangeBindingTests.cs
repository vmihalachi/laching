using System.Runtime.CompilerServices;
using Laching.Windows.Shell;

namespace Laching.Core.Tests.WindowsShell;

public sealed class ChangeBindingTests
{
    [Fact]
    public void LoadReloadAndUnloadFollowElementLifetimeWithoutDuplicateCallbacks()
    {
        var listeners = new ChangeListeners();
        var calls = 0;
        var binding = new ChangeBinding(listeners, () => calls++);
        binding.Apply();
        listeners.Raise();
        Assert.Equal(1, calls);
        binding.Loaded();
        binding.Loaded();
        listeners.Raise();
        Assert.Equal(4, calls);
        binding.Unloaded();
        listeners.Raise();
        Assert.Equal(4, calls);
        binding.Loaded();
        listeners.Raise();
        Assert.Equal(6, calls);
        GC.KeepAlive(binding);
    }

    [Fact]
    public void ElementsSharingRepaintDelegateHaveIndependentLifetimes()
    {
        var listeners = new ChangeListeners();
        var calls = 0;
        Action repaint = () => calls++;
        var first = new ChangeBinding(listeners, repaint);
        var second = new ChangeBinding(listeners, repaint);
        first.Loaded();
        second.Loaded();
        first.Unloaded();
        listeners.Raise();
        Assert.Equal(3, calls);
        GC.KeepAlive(first);
        GC.KeepAlive(second);
    }

    [Fact]
    public void UnloadingAnotherElementDuringRepaintSkipsItsCallback()
    {
        var listeners = new ChangeListeners();
        var calls = 0;
        var second = new ChangeBinding(listeners, () => calls++);
        var first = new ChangeBinding(listeners, second.Unloaded);
        first.Loaded();
        second.Loaded();
        listeners.Raise();
        Assert.Equal(1, calls);
        GC.KeepAlive(first);
        GC.KeepAlive(second);
    }

    [Fact]
    public void MissingUnloadedDoesNotRootElementThroughGlobalListeners()
    {
        var listeners = new ChangeListeners();
        var weak = AddAbandonedBinding(listeners);
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        Assert.False(weak.IsAlive);
        listeners.Raise();
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static WeakReference AddAbandonedBinding(ChangeListeners listeners)
    {
        var owner = new object();
        var binding = new ChangeBinding(listeners, () => GC.KeepAlive(owner));
        binding.Loaded();
        return new WeakReference(binding);
    }
}
