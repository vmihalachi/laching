using System.Runtime.InteropServices;
using System.Xml.Linq;
using Microsoft.Windows.AppLifecycle;
using Microsoft.Windows.AppNotifications;

namespace Laching.Windows.Shell;

/// <summary>
/// For a process Windows started for a notification click. The click goes to the process that registers for it, and
/// <c>AppInstance.GetActivatedEventArgs</c> waits for it there (it throws when nothing registered), so such a process
/// registers before reading its activation: through <see cref="AppNotifier{TAction}.Register"/>, or
/// <see cref="TakeClick"/> in a copy that only hands the click on.
/// </summary>
public static class AppNotifier
{
    /// <summary>
    /// What Windows puts on the command line when it starts the app for a click: the notification server's arguments,
    /// which <c>AppNotificationManager.Register</c> writes for an unpackaged app and a package's manifest declares
    /// (<c>com:ExeServer Arguments</c>).
    /// </summary>
    public const string ClickArgument = "----AppNotificationActivated:";

    /// <summary>
    /// Windows started this process for a click: <paramref name="arguments"/> (<c>Environment.GetCommandLineArgs()</c>)
    /// carry <see cref="ClickArgument"/>. Known before anything is registered, also when Windows reports the launch as a
    /// plain <see cref="ExtendedActivationKind.Launch"/>.
    /// </summary>
    public static bool IsClickLaunch(IEnumerable<string> arguments) =>
        arguments.Any(argument => argument.Contains(ClickArgument, StringComparison.Ordinal));

    /// <summary>
    /// The click this process was started for, in a copy that hands it to the copy already running: registers with no
    /// handler, so Windows gives this process that one click and starts a new one for any later, reads it, and
    /// unregisters. Null when Windows won't register the process, the click never comes, or the activation isn't a click
    /// (<see cref="IsClick"/>). Never throws.
    /// </summary>
    public static AppActivationArguments? TakeClick()
    {
        var manager = AppNotificationManager.Default;
        try
        {
            manager.Register();
        }
        catch (COMException)
        {
            return null;
        }
        try
        {
            var activation = AppInstance.GetCurrent().GetActivatedEventArgs();
            return activation is not null && IsClick(activation.Kind) ? activation : null;
        }
        catch (Exception e) when (e is TimeoutException or COMException)
        {
            // The click didn't arrive in Windows App SDK's wait (2 s): it went to a copy registered before this one.
            return null;
        }
        finally
        {
            try
            {
                manager.Unregister();
            }
            catch (COMException)
            {
                // Windows already dropped the registration.
            }
        }
    }

    /// <summary>
    /// The activation <see cref="TakeClick"/> hands over: a click only. Windows App SDK can report a click launch as a
    /// plain <see cref="ExtendedActivationKind.Launch"/> (a package's own activation, read before the command line), which
    /// the running copy would take as the user opening the app, and show Home for; dropping it loses the click instead.
    /// </summary>
    internal static bool IsClick(ExtendedActivationKind kind) => kind == ExtendedActivationKind.AppNotification;

    /// <summary>
    /// The notification's XML, in <c>AppNotificationBuilder</c>'s shape but escaped throughout: Windows App SDK 2.5's
    /// <c>AppNotificationButton</c> writes its title into the XML as it is, so one with an apostrophe ("Shh per
    /// un'ora"), an ampersand or a <c>&lt;</c> made the notification unbuildable.
    /// </summary>
    /// <param name="actionKey">The argument each click carries its action key under.</param>
    /// <param name="bannerAction">The action key of a click on the notification itself.</param>
    internal static string Payload(
        string actionKey,
        string bannerAction,
        string title,
        string body,
        IReadOnlyList<NotificationButton> buttons,
        bool sound
    )
    {
        if (buttons.Count > MaxButtons)
            throw new ArgumentException($"A notification takes up to {MaxButtons} buttons.", nameof(buttons));
        var binding = new XElement("binding", new XAttribute("template", "ToastGeneric"), new XElement("text", title));
        if (body.Length > 0)
            binding.Add(new XElement("text", body));
        var toast = new XElement("toast", new XAttribute("launch", Argument(actionKey, bannerAction)), new XElement("visual", binding));
        if (!sound)
            toast.Add(new XElement("audio", new XAttribute("silent", "true")));
        if (buttons.Count > 0)
        {
            toast.Add(
                new XElement(
                    "actions",
                    buttons.Select(button =>
                        new XElement(
                            "action",
                            new XAttribute("content", button.Title),
                            new XAttribute("arguments", Argument(actionKey, button.Action))
                        )
                    )
                )
            );
        }
        return toast.ToString(SaveOptions.DisableFormatting);
    }

    /// <summary>
    /// One <c>key=value</c> argument as <c>AppNotificationActivatedEventArgs.Arguments</c> reads it back: <c>%</c>,
    /// <c>;</c> and <c>=</c> percent-encoded, as the builder writes them.
    /// </summary>
    internal static string Argument(string key, string value) => Encode(key) + "=" + Encode(value);

    private const int MaxButtons = 5;

    private static string Encode(string text) => text.Replace("%", "%25").Replace(";", "%3B").Replace("=", "%3D");
}

/// <summary>
/// Windows app notifications with buttons that route back as <typeparamref name="TAction"/>, also when Windows
/// launched the app to handle the click. One notification at a time: posting replaces the one still in
/// Notification Center, and <see cref="IsShowingAsync"/> tells whether it is. Titles and bodies arrive already localized. One per process, since it owns
/// <c>AppNotificationManager.Default</c>. Made for a packaged app, whose identity comes from its MSIX: an unpackaged
/// build (Debug) registers, posts and reads the setting, but Windows 11 may never show its notifications (no banner, no
/// Notification Center entry, though <c>History</c> lists them), and after Quit a click on one left behind starts nothing.
/// </summary>
/// <typeparam name="TAction">The app's notion of what a click means (an enum, say).</typeparam>
public sealed class AppNotifier<TAction> : IDisposable
    where TAction : notnull
{
    private const string ActionKey = "action";

    private readonly IReadOnlyDictionary<string, TAction> actions;
    private readonly string defaultAction;
    private readonly string tag;
    private readonly string group;
    private readonly AppNotificationManager manager = AppNotificationManager.Default;
    private bool registered;

    /// <param name="actions">Action keys (short, stable, ASCII: they travel in the notification's arguments) to actions.</param>
    /// <param name="defaultAction">
    /// The key for a click on the notification itself, and for arguments the map doesn't know; must be in
    /// <paramref name="actions"/>.
    /// </param>
    /// <param name="tag">Identifies the app's notification, so a new one replaces the last.</param>
    /// <param name="group">The notification's group, paired with <paramref name="tag"/>.</param>
    public AppNotifier(IReadOnlyDictionary<string, TAction> actions, string defaultAction, string tag, string group)
    {
        if (!actions.ContainsKey(defaultAction))
            throw new ArgumentException("The default action must be one of the actions.", nameof(defaultAction));
        this.actions = actions;
        this.defaultAction = defaultAction;
        this.tag = tag;
        this.group = group;
    }

    /// <summary>A click on the notification or one of its buttons. Raised on a background thread.</summary>
    public event Action<TAction>? Invoked;

    /// <summary>Notifications are turned off for the app (or entirely), so nothing posted can show.</summary>
    public bool IsDenied => manager.Setting != AppNotificationSetting.Enabled;

    /// <summary>
    /// Subscribes and registers with Windows; call once at launch, before posting. A second call does nothing.
    /// </summary>
    public void Register()
    {
        if (registered)
            return;
        manager.NotificationInvoked += OnInvoked;
        try
        {
            manager.Register();
        }
        catch
        {
            manager.NotificationInvoked -= OnInvoked;
            throw;
        }
        registered = true;
    }

    /// <summary>The action a notification click carried, for the activation Windows launched the app with.</summary>
    public TAction ActionFrom(AppNotificationActivatedEventArgs args) =>
        args.Arguments.TryGetValue(ActionKey, out var raw) && actions.TryGetValue(raw, out var action)
            ? action
            : actions[defaultAction];

    /// <summary>
    /// Posts the notification, replacing the previous one. Up to five <paramref name="buttons"/>, each carrying one of
    /// the action keys; <paramref name="sound"/> false posts it silently. Windows takes it down itself at
    /// <paramref name="expiration"/>, if given.
    /// </summary>
    public async Task PostAsync(
        string title,
        string body,
        IReadOnlyList<NotificationButton> buttons,
        bool sound = true,
        DateTimeOffset? expiration = null
    )
    {
        var payload = AppNotifier.Payload(ActionKey, defaultAction, title, body, buttons, sound);
        var notification = new AppNotification(payload) { Tag = tag, Group = group };
        if (expiration is { } expires)
            notification.Expiration = expires;
        await RemoveAsync();
        manager.Show(notification);
    }

    /// <summary>
    /// The posted notification is still in Notification Center: the user hasn't clicked or cleared it, and it hasn't
    /// expired. False when Windows can't tell.
    /// </summary>
    public async Task<bool> IsShowingAsync()
    {
        try
        {
            var shown = await manager.GetAllAsync();
            return shown.Any(n => n.Tag == tag && n.Group == group);
        }
        catch (COMException)
        {
            return false;
        }
    }

    /// <summary>Takes the posted notification out of Notification Center, if it is still there.</summary>
    public async Task RemoveAsync()
    {
        try
        {
            await manager.RemoveByTagAndGroupAsync(tag, group);
        }
        catch (COMException)
        {
            // Nothing to remove.
        }
    }

    /// <summary>
    /// Unsubscribes and unregisters, only if <see cref="Register"/> did register: Windows throws when a process
    /// that never registered unregisters and the package has no registration left. Never throws.
    /// </summary>
    public void Dispose()
    {
        if (!registered)
            return;
        registered = false;
        manager.NotificationInvoked -= OnInvoked;
        try
        {
            manager.Unregister();
        }
        catch (COMException)
        {
            // Windows already dropped the registration ("Not Registered for App Notifications!").
        }
    }

    private void OnInvoked(AppNotificationManager sender, AppNotificationActivatedEventArgs args) =>
        Invoked?.Invoke(ActionFrom(args));
}
