using Laching.Companion.Shell.App;
using Laching.Windows.Shell;

namespace Laching.Windows.Companion.Shell;

/// <summary>
/// The model's menus (<see cref="CompanionMenus"/>) as native menu rows for <see cref="TrayIconHost"/>: the tray icon's
/// right-click menu and the desktop character's. A command that isn't enabled becomes a dimmed label.
/// </summary>
public static class MenuConversion
{
    public static IReadOnlyList<TrayMenuItem> ToTray(IEnumerable<MenuItem> items) => items.Select(ToTray).ToArray();

    public static TrayMenuItem ToTray(MenuItem item) =>
        item switch
        {
            MenuItem.Command { IsEnabled: false } command => new TrayMenuItem.Label(command.Title),
            MenuItem.Command command => new TrayMenuItem.Command(command.Title, command.Invoke, command.IsChecked),
            MenuItem.Submenu submenu => new TrayMenuItem.Submenu(submenu.Title, ToTray(submenu.Items)),
            _ => new TrayMenuItem.Separator(),
        };
}
