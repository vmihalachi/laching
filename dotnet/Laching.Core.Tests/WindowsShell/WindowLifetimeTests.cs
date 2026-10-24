using Laching.Windows.Shell;

namespace Laching.Core.Tests.WindowsShell;

public sealed class WindowLifetimeTests
{
    [Fact]
    public void ShowReusesAndActivatesExistingWindowWithoutCallingFactory()
    {
        var harness = new Harness();
        var window = harness.Windows.Show("home");
        Assert.Same(window, harness.Windows.Show("home", () => throw new Exception("must reuse")));
        Assert.Equal(2, window.Activations);
        Assert.Equal(1, harness.Windows.Count);
    }

    [Fact]
    public void ExclusiveWindowsShareOneSlotAndLeaveOrdinaryWindowsOpen()
    {
        var harness = new Harness();
        var home = harness.Windows.Show("home");
        var first = harness.Windows.Show("exclusive-one");
        var second = harness.Windows.Show("exclusive-two");
        Assert.Equal(1, first.Disposals);
        Assert.Same(home, harness.Windows.Find("home"));
        Assert.Same(second, harness.Windows.Find("exclusive-two"));
        Assert.Equal(2, harness.Windows.Count);
    }

    [Fact]
    public void ClosingRemovesAndDisposesBeforeCallingAppAndOnlyOnce()
    {
        var harness = new Harness();
        var window = harness.Windows.Show("home");
        var calls = 0;
        harness.Windows.Closed = id =>
        {
            Assert.Null(harness.Windows.Find(id));
            Assert.Null(harness.Windows.Active);
            Assert.Equal(1, window.Disposals);
            calls++;
        };
        harness.Windows.Close("home");
        harness.Windows.DidClose("home", window);
        Assert.Equal(1, calls);
    }

    [Fact]
    public void CloseCallbackCanReopenSameIdAndStaleEventsCannotRemoveIt()
    {
        var harness = new Harness();
        var old = harness.Windows.Show("home");
        FakeWindow? reopened = null;
        harness.Windows.Closed = id => reopened = harness.Windows.Show(id);
        harness.Windows.Close("home");
        harness.Windows.DidClose("home", old);
        harness.Windows.DidActivate("home", old, true);
        Assert.NotSame(old, reopened);
        Assert.Same(reopened, harness.Windows.Find("home"));
        Assert.Same(reopened, harness.Windows.Active);
    }

    [Fact]
    public void DisposalCanReopenSameId()
    {
        var harness = new Harness();
        var old = harness.Windows.Show("home");
        old.OnDispose = () => harness.Windows.Show("home");
        harness.Windows.Close("home");
        Assert.NotNull(harness.Windows.Find("home"));
        Assert.NotSame(old, harness.Windows.Find("home"));
    }

    [Fact]
    public void AppIsNotifiedEvenWhenContentDisposalThrows()
    {
        var harness = new Harness();
        var window = harness.Windows.Show("home");
        window.OnDispose = () => throw new InvalidOperationException("dispose failed");
        var calls = 0;
        harness.Windows.Closed = _ => calls++;
        Assert.Throws<InvalidOperationException>(() => harness.Windows.Close("home"));
        Assert.Equal(0, harness.Windows.Count);
        Assert.Equal(1, calls);
    }

    [Fact]
    public void ExclusiveCloseCallbackCanCloseTheWindowBeingOpened()
    {
        var harness = new Harness();
        harness.Windows.Show("exclusive-one");
        harness.Windows.Closed = id =>
        {
            if (id == "exclusive-one")
                harness.Windows.Close("exclusive-two");
        };
        var closedBeforeActivation = harness.Windows.Show("exclusive-two");
        Assert.Equal(0, closedBeforeActivation.Activations);
        Assert.Equal(1, closedBeforeActivation.Disposals);
        Assert.Equal(0, harness.Windows.Count);
    }

    [Fact]
    public void CloseAllDoesNotCloseAReplacementOpenedByCallback()
    {
        var harness = new Harness();
        harness.Windows.Show("home");
        var second = harness.Windows.Show("settings");
        FakeWindow? reopened = null;
        harness.Windows.Closed = id =>
        {
            if (id != "home")
                return;
            harness.Windows.Close("settings");
            reopened = harness.Windows.Show("settings");
        };
        harness.Windows.CloseAll();
        Assert.Equal(1, second.Disposals);
        Assert.Same(reopened, harness.Windows.Find("settings"));
        Assert.Equal(0, reopened!.Disposals);
    }

    [Fact]
    public void ActivationUsesCurrentWindowWhenDeactivationEventsArriveLate()
    {
        var harness = new Harness();
        var first = harness.Windows.Show("home");
        var second = harness.Windows.Show("settings");
        harness.Windows.DidActivate("home", first, false);
        Assert.Same(second, harness.Windows.Active);
        harness.Windows.DidActivate("settings", second, false);
        Assert.Null(harness.Windows.Active);
    }

    [Fact]
    public void ShowOpenWindowsRestoresEachCurrentWindowAndReportsEmptyState()
    {
        var harness = new Harness();
        Assert.False(harness.Windows.ShowOpenWindows());
        var home = harness.Windows.Show("home");
        var settings = harness.Windows.Show("settings");
        Assert.True(harness.Windows.ShowOpenWindows());
        Assert.Equal(2, home.Activations);
        Assert.Equal(2, settings.Activations);
    }

    private sealed class Harness
    {
        public WindowLifetime<string, FakeWindow> Windows { get; }
        public Harness()
        {
            Windows = new(
                id => new(id),
                id => id.StartsWith("exclusive", StringComparison.Ordinal),
                window => Windows!.DidClose(window.Id, window),
                window => { window.Activations++; Windows!.DidActivate(window.Id, window, true); },
                window => window
            );
        }
    }

    private sealed class FakeWindow(string id) : IDisposable
    {
        public string Id { get; } = id;
        public int Activations { get; set; }
        public int Disposals { get; private set; }
        public Action? OnDispose { get; set; }
        public void Dispose() { Disposals++; OnDispose?.Invoke(); }
    }
}
