#if os(macOS)
    import AppKit
    import Foundation
    import LachingShell

    /// What `CompanionAppDelegate` starts: a `CompanionAppModel` (any of them).
    @MainActor
    public protocol CompanionLaunching: AnyObject {
        func launch(showHome: Bool)
        func reopen()
    }

    extension CompanionAppModel: CompanionLaunching {}

    /// Starts the app's model once the app has launched. Home is shown unless macOS started the app at login
    /// (`LaunchContext.launchedAtLogin(appleEvent:)`: the launch Apple Event, there only while
    /// `applicationDidFinishLaunching` runs, or a login item started just after the session); the first run shows
    /// onboarding instead.
    /// Tests host the app without launching it; a Debug screenshot run can take over instead (`runScreenshots()`).
    ///
    /// Not generic (AppKit calls a delegate through Objective-C): the app subclasses it, holds its own model and hands it
    /// over as `companion`.
    ///
    ///     final class AppDelegate: CompanionAppDelegate {
    ///         let model = AppModel()
    ///         override var companion: any CompanionLaunching { model }
    ///     }
    @MainActor
    open class CompanionAppDelegate: NSObject, NSApplicationDelegate {
        /// The app is hosting unit tests (`XCTestConfigurationFilePath` is set): nothing launches.
        public static var isRunningTests: Bool {
            ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
        }

        override public init() {
            super.init()
        }

        /// *Required.* The app's model.
        open var companion: any CompanionLaunching {
            fatalError("\(type(of: self)) must override companion")
        }

        /// Runs a screenshot session instead of launching, when the app is in one (Debug); true when it took over.
        open func runScreenshots() -> Bool { false }

        open func applicationDidFinishLaunching(_ notification: Notification) {
            // Read first: the launch Apple Event is gone once this returns (and not there yet in willFinishLaunching).
            let launchedAsLoginItem = LaunchContext.launchedAsLoginItem()
            guard !Self.isRunningTests, !runScreenshots() else { return }
            companion.launch(showHome: !LaunchContext.launchedAtLogin(appleEvent: launchedAsLoginItem))
        }

        /// Opening the app while it runs (Finder, Launchpad, Spotlight): Home again, or the open windows.
        open func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
            guard !Self.isRunningTests else { return true }
            companion.reopen()
            return false
        }
    }
#endif
