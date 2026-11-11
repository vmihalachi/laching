using System.Runtime.InteropServices;
using System.Text;
using Laching.Windows.System;
using Microsoft.UI.Dispatching;
using Windows.Graphics;

namespace Laching.Windows.Shell;

/// <summary>
/// The notification-area icon (<c>Shell_NotifyIcon</c>) and its native menu, opened by a right click or the
/// keyboard's menu key, and by a left click unless <see cref="Selected"/> has a handler. The owner rebuilds the menu
/// with <see cref="SetMenu"/> and the glyph with <see cref="SetGlyph"/>; the glyph is redrawn when the taskbar
/// switches between light and dark and re-added when Explorer restarts. A hidden window on the UI thread receives
/// the icon's messages, so create the host there; it takes the tooltip as its name, which Narrator reads while the menu
/// opens. The menu is dark while the taskbar is, outside a contrast theme. A menu opened and cancelled from the keyboard gives the focus back to the notification area
/// (<c>NIM_SETFOCUS</c>), not to that hidden window. Menu words show every "&amp;" as written unless
/// <see cref="AccessKeys"/> is on.
/// </summary>
public sealed class TrayIconHost : IDisposable
{
    private const uint CallbackMessage = NativeMethods.WM_APP + 1;

    /// <summary>The icon's id under its window when Windows refuses the GUID.</summary>
    private const uint FallbackId = 1;

    /// <summary>How far (pixels) the pointer may be off the icon and still count as having clicked it.</summary>
    private const int PointerSlop = 8;

    /// <summary>Broadcast when Explorer (re)starts, which drops every icon.</summary>
    private static readonly uint TaskbarCreated = NativeMethods.RegisterWindowMessageW("TaskbarCreated");

    private readonly Guid iconId;
    private readonly MessageWindow window;
    private readonly AppearanceWatcher appearance = new();
    private readonly DispatcherQueue dispatcher;
    private IReadOnlyList<TrayMenuItem> menu = [];
    private Func<bool, nint>? glyph;
    private nint hicon;
    private string toolTip = "";
    private bool added;

    /// <summary>Whether the icon is known by <see cref="iconId"/>; false once Windows refused it.</summary>
    private bool byGuid = true;

    /// <param name="dispatcher">The UI thread's queue; menu commands and <see cref="Selected"/> run on it.</param>
    /// <param name="iconId">
    /// Identifies the icon to Windows, which remembers its taskbar settings by this GUID and ties the GUID to one
    /// executable path. Pick one per app (and another for a Dev build) and never change it.
    /// </param>
    /// <param name="windowClassName">The hidden window's class name, unique within the process.</param>
    public TrayIconHost(DispatcherQueue dispatcher, Guid iconId, string windowClassName = "Laching.Tray")
    {
        this.dispatcher = dispatcher;
        this.iconId = iconId;
        // Hidden and top-level rather than message-only: only top-level windows get TaskbarCreated.
        window = new MessageWindow(windowClassName, messageOnly: false, OnMessage);
        // The taskbar follows Windows' mode (not the app's), so redraw when it changes.
        appearance.Changed += () => dispatcher.TryEnqueue(Redraw);
    }

    /// <summary>
    /// A left click on the icon, with its anchor point in screen pixels (for a flyout). Raised on the UI thread.
    /// With no handler, a left click opens the menu instead.
    /// </summary>
    public event Action<PointInt32>? Selected;

    /// <summary>
    /// The menu is about to open (a right click, the menu key, or <see cref="ShowMenu"/>), so an owner whose menu
    /// follows its state can <see cref="SetMenu"/> it now. Raised on the UI thread.
    /// </summary>
    public event Action? MenuOpening;

    /// <summary>
    /// Whether the latest click or menu on the icon came from the keyboard (Enter, Space, Shift+F10 or the menu key on
    /// the focused icon) rather than the pointer: true when the pointer wasn't on the icon. Read it in
    /// <see cref="Selected"/> to show the focus the way the user came, as a flyout opened from the keyboard should.
    /// </summary>
    public bool ByKeyboard { get; private set; }

    /// <summary>
    /// Whether the menu's words carry their own access keys, written the Win32 way: a single "&amp;" marks the next
    /// character as the item's key and "&amp;&amp;" shows one "&amp;". Off by default: every "&amp;" shows as written,
    /// and Windows takes each item's first letter, which items may share. Turn it on only with words written for it;
    /// the same words shown anywhere else (a panel's rows) go through <see cref="WithoutAccessKeys"/> there.
    /// </summary>
    public bool AccessKeys { get; set; }

    /// <summary>The small-icon size at the system DPI (16 px at 100 %), for drawing glyphs.</summary>
    public static int SmallIconPixels => Math.Max(16, NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSMICON));

    /// <summary>
    /// Shows the icon drawn by <paramref name="draw"/>, which gets whether the taskbar is light and returns an
    /// <c>HICON</c> that this class frees. <paramref name="toolTip"/> is cut to 127 characters.
    /// </summary>
    public void SetGlyph(Func<bool, nint> draw, string toolTip)
    {
        glyph = draw;
        if (toolTip != this.toolTip)
            window.SetName(toolTip);
        this.toolTip = toolTip;
        Redraw();
    }

    /// <summary>The menu's rows, shown from the next time it opens.</summary>
    public void SetMenu(IEnumerable<TrayMenuItem> items) => menu = items.ToArray();

    /// <summary>The icon's bounds in screen pixels, for placing a flyout next to it; false while it isn't shown.</summary>
    public bool TryGetIconRect(out RectInt32 rect)
    {
        var identifier = new NativeMethods.NOTIFYICONIDENTIFIER
        {
            cbSize = (uint)Marshal.SizeOf<NativeMethods.NOTIFYICONIDENTIFIER>(),
            hWnd = window.Handle,
            uID = byGuid ? 0 : FallbackId,
            guidItem = byGuid ? iconId : Guid.Empty,
        };
        if (added && NativeMethods.Shell_NotifyIconGetRect(identifier, out var location) == 0)
        {
            rect = new RectInt32(
                location.Left,
                location.Top,
                location.Right - location.Left,
                location.Bottom - location.Top
            );
            return true;
        }
        rect = default;
        return false;
    }

    /// <summary>Opens the menu at a screen point, as a right click does.</summary>
    public void ShowMenu(PointInt32 anchor) => ShowMenu(anchor, fromKeyboard: false);

    /// <summary>
    /// <paramref name="text"/> as it reads with its access-key marks taken out: a single "&amp;" dropped and
    /// "&amp;&amp;" shown as "&amp;". For words written for <see cref="AccessKeys"/> that also show outside the menu.
    /// </summary>
    public static string WithoutAccessKeys(string text)
    {
        if (!text.Contains('&', StringComparison.Ordinal))
            return text;
        var plain = new StringBuilder(text.Length);
        for (var i = 0; i < text.Length; i++)
        {
            if (text[i] != '&')
                plain.Append(text[i]);
            else if (i + 1 < text.Length && text[i + 1] == '&')
                plain.Append(text[++i]);
        }
        return plain.ToString();
    }

    /// <summary>A row's words as the menu takes them: as written with <paramref name="accessKeys"/>, else every "&amp;" doubled so it shows.</summary>
    internal static string MenuText(string text, bool accessKeys) => accessKeys ? text : text.Replace("&", "&&", StringComparison.Ordinal);

    /// <summary>
    /// <see cref="ByKeyboard"/> from where the pointer is: on the icon (give or take a few pixels) after a click on it, or,
    /// when the icon's bounds are unknown, at <paramref name="anchor"/>, the point a click reports.
    /// </summary>
    internal static bool IsPointerOn(RectInt32? icon, PointInt32 anchor, PointInt32 pointer) =>
        icon is { } rect
            ? pointer.X >= rect.X - PointerSlop
                && pointer.X <= rect.X + rect.Width + PointerSlop
                && pointer.Y >= rect.Y - PointerSlop
                && pointer.Y <= rect.Y + rect.Height + PointerSlop
            : Math.Abs(pointer.X - anchor.X) <= PointerSlop && Math.Abs(pointer.Y - anchor.Y) <= PointerSlop;

    /// <summary>Whether the menu draws dark: as the taskbar does, unless a contrast theme is on.</summary>
    internal static bool IsMenuDark(bool taskbarLight, bool contrastTheme) => !taskbarLight && !contrastTheme;

    private static bool ContrastThemeIsOn => new global::Windows.UI.ViewManagement.AccessibilitySettings().HighContrast;

    private void ShowMenu(PointInt32 anchor, bool fromKeyboard)
    {
        MenuOpening?.Invoke();
        if (menu.Count == 0 || window.Handle == 0)
            return;
        var commands = new List<Action>();
        var handle = Build(menu, commands);
        var align =
            NativeMethods.GetSystemMetrics(NativeMethods.SM_MENUDROPALIGNMENT) != 0
                ? NativeMethods.TPM_RIGHTALIGN
                : NativeMethods.TPM_LEFTALIGN;
        // Win32 menus are light unless the process asks for dark ones: dark while it's open when the taskbar is, read
        // at each opening so a switch of Windows' mode shows at the next. Back to the default afterwards, so the
        // process's other menus (a window's system menu) keep theirs. A contrast theme draws its own colors.
        var dark = IsMenuDark(AppearanceWatcher.IsTaskbarLight, ContrastThemeIsOn);
        if (dark)
            NativeMethods.SetPreferredAppMode(NativeMethods.AppModeForceDark);
        // The foreground window and the WM_NULL afterwards make the menu close when you click elsewhere.
        NativeMethods.SetForegroundWindow(window.Handle);
        var chosen = NativeMethods.TrackPopupMenuEx(
            handle,
            NativeMethods.TPM_RETURNCMD
                | NativeMethods.TPM_NONOTIFY
                | NativeMethods.TPM_RIGHTBUTTON
                | NativeMethods.TPM_BOTTOMALIGN
                | align,
            anchor.X,
            anchor.Y,
            window.Handle,
            0
        );
        NativeMethods.PostMessageW(window.Handle, NativeMethods.WM_NULL, 0, 0);
        NativeMethods.DestroyMenu(handle);
        if (dark)
            NativeMethods.SetPreferredAppMode(NativeMethods.AppModeDefault);
        // Queued, so a command (Quit, say) never runs inside this window's message handler.
        if (chosen > 0)
            dispatcher.TryEnqueue(() => commands[chosen - 1]());
        // Cancelled from the keyboard (Esc), the focus would stay on this hidden window: back to the notification area,
        // as Windows asks. Not when a click elsewhere cancelled it and gave that window the foreground.
        else if (fromKeyboard && added && NativeMethods.GetForegroundWindow() == window.Handle)
            Notify(NativeMethods.NIM_SETFOCUS, 0);
    }

    public void Dispose()
    {
        appearance.Dispose();
        if (added)
            Notify(NativeMethods.NIM_DELETE, 0);
        window.Dispose();
        DestroyIcon(hicon);
        hicon = 0;
    }

    private void Redraw()
    {
        if (glyph is null)
            return;
        var old = hicon;
        hicon = glyph(AppearanceWatcher.IsTaskbarLight);
        Show();
        DestroyIcon(old);
    }

    /// <summary>Updates the icon and tooltip, adding the icon if it isn't there.</summary>
    private void Show()
    {
        const uint Content = NativeMethods.NIF_ICON | NativeMethods.NIF_TIP | NativeMethods.NIF_SHOWTIP;
        if (window.Handle == 0)
            return;
        if (added && Notify(NativeMethods.NIM_MODIFY, Content))
            return;
        // An icon left behind with our GUID (a crash, say) makes NIM_ADD fail.
        Notify(NativeMethods.NIM_DELETE, 0);
        added = Add();
        // Windows ties the GUID to the executable that first used it, so the same app built in another folder (a
        // second checkout, the other architecture) is refused: that copy goes by window and id instead, and its
        // taskbar settings aren't remembered.
        if (!added && byGuid)
        {
            byGuid = false;
            added = Add();
        }

        bool Add() => Notify(NativeMethods.NIM_ADD, Content | NativeMethods.NIF_MESSAGE) && Notify(NativeMethods.NIM_SETVERSION, 0);
    }

    private unsafe bool Notify(uint message, uint flags)
    {
        var data = new NativeMethods.NOTIFYICONDATAW
        {
            cbSize = (uint)sizeof(NativeMethods.NOTIFYICONDATAW),
            hWnd = window.Handle,
            uID = byGuid ? 0 : FallbackId,
            uFlags = flags | (byGuid ? NativeMethods.NIF_GUID : 0),
            uCallbackMessage = CallbackMessage,
            hIcon = hicon,
            uVersion = NativeMethods.NOTIFYICON_VERSION_4,
            guidItem = byGuid ? iconId : Guid.Empty,
        };
        // 127 characters and the terminator the zeroed buffer already has.
        toolTip.AsSpan(0, Math.Min(toolTip.Length, 127)).CopyTo(new Span<char>(data.szTip, 128));
        return NativeMethods.Shell_NotifyIconW(message, &data);
    }

    private nint? OnMessage(uint message, nint wParam, nint lParam)
    {
        if (message == CallbackMessage)
        {
            // Version 4 puts the event in lParam's low word and the icon's anchor point in wParam.
            var anchor = new PointInt32((short)(wParam & 0xFFFF), (short)((wParam >> 16) & 0xFFFF));
            var kind = (uint)(lParam & 0xFFFF);
            if (kind is NativeMethods.WM_LBUTTONUP or NativeMethods.WM_CONTEXTMENU)
                ByKeyboard = !IsPointerOn(TryGetIconRect(out var icon) ? icon : null, anchor, Pointer());
            switch (kind)
            {
                case NativeMethods.WM_LBUTTONUP when Selected is { } selected:
                    dispatcher.TryEnqueue(() => selected(anchor));
                    break;
                case NativeMethods.WM_LBUTTONUP:
                case NativeMethods.WM_CONTEXTMENU:
                    ShowMenu(anchor, ByKeyboard);
                    break;
            }
            return 0;
        }
        if (message == TaskbarCreated && TaskbarCreated != 0)
        {
            added = false;
            Redraw();
            return 0;
        }
        return null;
    }

    private static PointInt32 Pointer() => NativeMethods.GetCursorPos(out var point) ? new PointInt32(point.X, point.Y) : default;

    /// <summary>A menu for <paramref name="items"/>; a command's ID is its position in <paramref name="commands"/> plus one.</summary>
    private nint Build(IEnumerable<TrayMenuItem> items, List<Action> commands)
    {
        var handle = NativeMethods.CreatePopupMenu();
        foreach (var item in items)
        {
            switch (item)
            {
                case TrayMenuItem.Command command:
                    commands.Add(command.Invoke);
                    var check = command.IsChecked ? NativeMethods.MF_CHECKED : 0;
                    NativeMethods.AppendMenuW(handle, NativeMethods.MF_STRING | check, (nuint)commands.Count, MenuText(command.Text, AccessKeys));
                    break;
                case TrayMenuItem.Label label:
                    NativeMethods.AppendMenuW(handle, NativeMethods.MF_STRING | NativeMethods.MF_GRAYED, 0, MenuText(label.Text, AccessKeys));
                    break;
                case TrayMenuItem.Submenu submenu:
                    var child = Build(submenu.Items, commands);
                    NativeMethods.AppendMenuW(handle, NativeMethods.MF_POPUP, (nuint)child, MenuText(submenu.Text, AccessKeys));
                    break;
                default:
                    NativeMethods.AppendMenuW(handle, NativeMethods.MF_SEPARATOR, 0, null);
                    break;
            }
        }
        return handle;
    }

    private static void DestroyIcon(nint icon)
    {
        if (icon != 0)
            NativeMethods.DestroyIcon(icon);
    }
}
