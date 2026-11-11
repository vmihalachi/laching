using System.Runtime.InteropServices;
using Laching.Companion.Shell.App;
using Laching.Windows.Shell;
using Microsoft.Windows.AppLifecycle;
using Microsoft.Windows.AppNotifications;

namespace Laching.Windows.Companion.Shell;

/// <summary>
/// The character's line as a Windows notification (<see cref="ICompanionNotifying"/>, Swift's <c>CompanionNotifier</c>)
/// over <see cref="AppNotifier{TAction}"/>, with the app's buttons: each action id travels in the notification and
/// comes back through <see cref="OnAction"/> on the UI thread, also when Windows started the app for the click. One tag, so a new line replaces the last instead of piling
/// up in Notification Center. Posted silently. A click on the banner itself does nothing but bring Windows' focus (as on
/// the Mac). Windows never asks for permission: it is on unless the user turned the app's notifications off. It registers
/// with Windows as it's made, unlike the Mac's at <see cref="Configure"/>: Windows hands the click it started the app for
/// to the registered process, so the app makes it before reading its activation (<see cref="CompanionApp"/> does).
/// </summary>
public sealed class CompanionNotifier : ICompanionNotifying, IDisposable
{
    /// <summary>The banner's own click, which no button carries.</summary>
    private const string BannerAction = "banner";

    private readonly AppNotifier<string> notifier;
    private readonly string title;
    private readonly IReadOnlyList<NotificationButton> buttons;
    private readonly SynchronizationContext ui;
    private bool available = true;
    private bool configured;

    /// <param name="title">The notification's first line (the app's or the character's name), already localized.</param>
    /// <param name="actions">The buttons, in order: action id (short, stable, ASCII; not "banner") and localized title.</param>
    /// <param name="tag">Identifies the app's notification, so a new one replaces the last.</param>
    /// <param name="ui">Where <see cref="OnAction"/> runs; the current thread's context by default.</param>
    public CompanionNotifier(
        string title,
        IReadOnlyList<(string Id, string Title)> actions,
        string tag = "line",
        SynchronizationContext? ui = null
    )
    {
        this.title = title;
        this.ui = UiThread.Resolve(ui, "notifier");
        buttons = actions.Select(action => new NotificationButton(action.Title, action.Id)).ToArray();
        var keys = actions.ToDictionary(action => action.Id, action => action.Id);
        keys[BannerAction] = BannerAction;
        notifier = new AppNotifier<string>(keys, BannerAction, tag, group: tag);
        notifier.Invoked += action => this.ui.Post(_ => Raise(action), null);
        try
        {
            notifier.Register();
        }
        catch (COMException)
        {
            available = false;
        }
    }

    public Action<string>? OnAction { get; set; }

    /// <summary>
    /// Takes the clicks: those while the app runs, those another copy hands over, and the one Windows started the app for
    /// (raised once the launch is done). A process Windows wouldn't register (no identity it accepts) takes none and posts
    /// nothing.
    /// </summary>
    public void Configure()
    {
        if (configured)
            return;
        configured = true;
        var instance = AppInstance.GetCurrent();
        instance.Activated += Redirected;
        if (!available)
            return;
        try
        {
            Clicked(instance.GetActivatedEventArgs());
        }
        catch (Exception e) when (e is TimeoutException or COMException)
        {
            // Started for a click that didn't arrive in Windows App SDK's wait (2 s).
        }
    }

    public void RequestAuthorization(Action<bool> completion) => completion(IsAllowed);

    public void RefreshAuthorization(Action<NotificationPermission> completion) =>
        completion(IsAllowed ? NotificationPermission.Authorized : NotificationPermission.Denied);

    public void Post(string line)
    {
        if (available)
            _ = PostAsync(line);
    }

    /// <summary>Unregisters, so a click on a notification left in Notification Center starts the app again.</summary>
    public void Dispose()
    {
        if (configured)
            AppInstance.GetCurrent().Activated -= Redirected;
        notifier.Dispose();
    }

    private bool IsAllowed => available && !notifier.IsDenied;

    /// <summary>
    /// A click that started another copy, which handed it here (rare: Windows gives a registered app its clicks). Read
    /// before returning: the click's arguments live in that copy, which exits once the handlers return.
    /// </summary>
    private void Redirected(object? sender, AppActivationArguments activation) => Clicked(activation);

    /// <summary>Posted: the launch finishes first, and a redirected click (on a background thread) reaches the UI thread.</summary>
    private void Clicked(AppActivationArguments activation)
    {
        if (activation.Kind != ExtendedActivationKind.AppNotification || activation.Data is not AppNotificationActivatedEventArgs args)
            return;
        var action = notifier.ActionFrom(args);
        ui.Post(_ => Raise(action), null);
    }

    private async Task PostAsync(string line)
    {
        try
        {
            await notifier.PostAsync(title, line, buttons, sound: false);
        }
        catch (COMException)
        {
            // Notifications switched off between the check and the post: the line goes unseen, as it would on the Mac.
        }
    }

    private void Raise(string action)
    {
        if (action != BannerAction)
            OnAction?.Invoke(action);
    }
}
