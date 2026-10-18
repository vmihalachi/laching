using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Windows.Graphics;
using static Laching.Windows.Shell.NativeMethods;

namespace Laching.Windows.Companion.Shell;

/// <summary>
/// Win32 calls the Windows App SDK doesn't wrap, past the ones <c>Laching.Windows.Shell.NativeMethods</c> has (POINT, RECT
/// and every import that takes one, the cursor and the foreground window, declared there once). Internal: the services
/// are the API.
/// </summary>
internal static partial class NativeMethods
{
    // MARK: Scale

    /// <summary>The DPI a window is drawn at when its scale is 100 %.</summary>
    public const double DefaultDpi = 96;

    [LibraryImport("user32.dll")]
    public static partial uint GetDpiForWindow(nint hwnd);

    private const uint MONITOR_DEFAULTTONEAREST = 2;

    /// <summary>MONITOR_DPI_TYPE::MDT_EFFECTIVE_DPI: the scale the user picked for the display.</summary>
    private const int MDT_EFFECTIVE_DPI = 0;

    [LibraryImport("shcore.dll")]
    private static partial int GetDpiForMonitor(nint monitor, int dpiType, out uint dpiX, out uint dpiY);

    /// <summary>A window's scale (1 at 96 DPI, 2 at 192), or 1 when it has none yet.</summary>
    public static double Scale(nint hwnd)
    {
        var dpi = hwnd == 0 ? 0 : GetDpiForWindow(hwnd);
        return dpi == 0 ? 1 : dpi / DefaultDpi;
    }

    /// <summary>The scale of the display at a screen point (the nearest one), for sizing a window before it is there.</summary>
    public static double Scale(int x, int y)
    {
        var monitor = MonitorFromPoint(new POINT { X = x, Y = y }, MONITOR_DEFAULTTONEAREST);
        return monitor != 0 && GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, out var dpi, out _) == 0 && dpi > 0
            ? dpi / DefaultDpi
            : 1;
    }

    /// <summary>The pointer's position in screen pixels, or the origin when it can't be read.</summary>
    public static (int X, int Y) Cursor() => GetCursorPos(out var point) ? (point.X, point.Y) : (0, 0);

    // MARK: Window frames

    /// <summary>
    /// Gives the window a client area of <paramref name="client"/> pixels. Not <c>AppWindow.ResizeClient</c>: for a
    /// window without a title bar it still adds a caption's height, so the frame is measured from the window instead.
    /// </summary>
    public static void SetClientSize(AppWindow window, SizeInt32 client)
    {
        var frameWidth = window.Size.Width - window.ClientSize.Width;
        var frameHeight = window.Size.Height - window.ClientSize.Height;
        window.Resize(new SizeInt32(client.Width + frameWidth, client.Height + frameHeight));
    }

    // MARK: The compositor (DWM)

    private const uint DWMWA_EXTENDED_FRAME_BOUNDS = 9;
    private const uint DWMWA_CLOAK = 13;
    private const uint DWMWA_WINDOW_CORNER_PREFERENCE = 33;
    private const uint DWMWA_BORDER_COLOR = 34;
    private const uint DWMWCP_DONOTROUND = 1;
    private const uint DWMWA_COLOR_NONE = 0xFFFFFFFE;

    [LibraryImport("dwmapi.dll")]
    private static partial int DwmSetWindowAttribute(nint hwnd, uint attribute, in uint value, uint size);

    /// <summary>No Windows 11 rounded corners and no accent border around the window: square, edge to edge.</summary>
    public static void SquareCorners(nint hwnd)
    {
        DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND, sizeof(uint));
        DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE, sizeof(uint));
    }

    /// <summary>The window's frame as the screen shows it, without the invisible resize border; <paramref name="outer"/> when the compositor can't say.</summary>
    public static RECT VisibleFrame(nint hwnd, RECT outer) =>
        DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, out var visible, Marshal.SizeOf<RECT>()) == 0 ? visible : outer;

    /// <summary>Blocks until the compositor has presented what this process has drawn so far (DirectComposition content included).</summary>
    [LibraryImport("dwmapi.dll")]
    public static partial int DwmFlush();

    // MARK: Hidden windows (a hidden screenshot run, the window manager's)

    private static readonly nint HWND_BOTTOM = 1;
    private const uint SWP_NOSIZE = 0x0001;
    private const uint SWP_NOMOVE = 0x0002;
    private const uint SWP_NOACTIVATE = 0x0010;

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetWindowPos(nint hwnd, nint after, int x, int y, int width, int height, uint flags);

    /// <summary>
    /// Shows the window where nobody sees it: cloaked (the compositor still draws it, so <c>PrintWindow</c> captures it,
    /// but never puts it on screen), out of the taskbar and Alt+Tab, behind every other window and without taking the focus.
    /// </summary>
    public static void ShowHidden(AppWindow window)
    {
        var hwnd = Microsoft.UI.Win32Interop.GetWindowFromWindowId(window.Id);
        DwmSetWindowAttribute(hwnd, DWMWA_CLOAK, 1, sizeof(uint));
        window.IsShownInSwitchers = false;
        window.Show(activateWindow: false);
        SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE);
    }

    // MARK: Screenshots

    /// <summary>PrintWindow's flag for what the compositor draws (DirectComposition content included).</summary>
    public const uint PW_RENDERFULLCONTENT = 2;

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool PrintWindow(nint hwnd, nint hdc, uint flags);

    // MARK: The desktop window (3.1's recipe): styles, click-through, its region, its owner

    public const int GWL_EXSTYLE = -20;
    public const int GWLP_HWNDPARENT = -8;
    public const long WS_EX_TRANSPARENT = 0x00000020;
    public const long WS_EX_TOOLWINDOW = 0x00000080;
    public const long WS_EX_LAYERED = 0x00080000;
    public const long WS_EX_NOACTIVATE = 0x08000000;
    public const long WS_EX_APPWINDOW = 0x00040000;

    [LibraryImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    public static partial nint GetWindowLongPtr(nint hwnd, int index);

    [LibraryImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
    public static partial nint SetWindowLongPtr(nint hwnd, int index, nint value);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool IsWindowVisible(nint hwnd);

    public const int RGN_OR = 2;

    [LibraryImport("gdi32.dll")]
    public static partial nint CreateRectRgn(int left, int top, int right, int bottom);

    [LibraryImport("gdi32.dll")]
    public static partial int CombineRgn(nint destination, nint source1, nint source2, int mode);

    [LibraryImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool DeleteObject(nint handle);

    /// <summary>Windows owns <paramref name="region"/> once this succeeds; 0 takes the region away.</summary>
    [LibraryImport("user32.dll")]
    public static partial int SetWindowRgn(nint hwnd, nint region, [MarshalAs(UnmanagedType.Bool)] bool redraw);

    [StructLayout(LayoutKind.Sequential)]
    public struct DispatcherQueueOptions
    {
        public int dwSize;
        public int threadType;
        public int apartmentType;
    }

    /// <summary>A <c>Windows.System.DispatcherQueue</c> for the thread, which the system compositor needs.</summary>
    [LibraryImport("CoreMessaging.dll")]
    public static partial int CreateDispatcherQueueController(DispatcherQueueOptions options, out nint controller);

    // MARK: Languages

    /// <summary>MUI_LANGUAGE_NAME: languages given by name ("de-DE"), not by number.</summary>
    private const uint MUI_LANGUAGE_NAME = 0x8;

    /// <summary>Takes a double-null-terminated list; skips the names it doesn't know, and fails only when it knows none.</summary>
    [LibraryImport("kernel32.dll", StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetProcessPreferredUILanguages(uint flags, string languages, out uint count);

    /// <summary>
    /// Makes <paramref name="languages"/> (BCP-47 tags, in order) this process's preferred UI languages, ahead of
    /// Windows' own: the language WinUI's own words come in when it starts. False when Windows took none of them.
    /// </summary>
    public static bool SetProcessLanguages(IEnumerable<string> languages) =>
        SetProcessPreferredUILanguages(MUI_LANGUAGE_NAME, string.Join('\0', languages) + "\0\0", out _);
}
