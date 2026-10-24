#if os(macOS)
    import AppKit
    import Darwin
    import Foundation
    import os

    /// How the app was started. Reads the launch Apple Event, which AppKit hands over only while it is being
    /// handled: call `launchedAsLoginItem()` from `applicationDidFinishLaunching`, before anything async, and
    /// keep the answer. In `applicationWillFinishLaunching` the event hasn't arrived yet, and after
    /// `applicationDidFinishLaunching` returns it's gone: both read as not a login item.
    /// Reads only; never registers or changes anything.
    public enum LaunchContext {
        /// How soon after the session started a launch still counts as the login item's when the Apple Event doesn't
        /// say so.
        public static let loginGrace: TimeInterval = 120

        /// Whether macOS started the app as a login item (`kAEOpenApplication` with
        /// `keyAELaunchedAsLogInItem`), as opposed to the user opening it.
        public static func launchedAsLoginItem() -> Bool {
            let event = NSAppleEventManager.shared().currentAppleEvent
            return event?.eventID == kAEOpenApplication
                && event?.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
        }

        /// Whether the app was started at login: the Apple Event says so (`appleEvent`, `launchedAsLoginItem()`'s
        /// answer, read in time), or the app is a login item (`LoginItem.isEnabled`) and its process started within
        /// `loginGrace` of the session (Finder's or the Dock's start, whichever came first), for a login launch the
        /// event doesn't mark. Logs what it read (subsystem `laching`, category `launch`, at notice level, so
        /// `log show` still has it after a login).
        public static func launchedAtLogin(appleEvent: Bool) -> Bool {
            let loginItem = LoginItem.isEnabled
            let since = sessionStart().map { (processStart(getpid()) ?? Date()).timeIntervalSince($0) }
            let atLogin = isAtLogin(appleEvent: appleEvent, loginItem: loginItem, sinceSessionStart: since)
            let seconds = since.map { String(format: "%.1f s", $0) } ?? "unknown"
            Logger(subsystem: "laching", category: "launch").notice(
                "login item event \(appleEvent, privacy: .public), login item \(loginItem, privacy: .public), started \(seconds, privacy: .public) after the session: at login \(atLogin, privacy: .public)"
            )
            return atLogin
        }

        /// `launchedAtLogin(appleEvent:)`'s rule over what it read: the Apple Event's answer, whether the app is a
        /// login item, and the seconds from the session's start to the app's (nil when unknown).
        public static func isAtLogin(appleEvent: Bool, loginItem: Bool, sinceSessionStart: TimeInterval?) -> Bool {
            if appleEvent { return true }
            guard loginItem, let since = sinceSessionStart else { return false }
            return since >= 0 && since < loginGrace
        }

        /// When the user's session started: the earlier start of its Finder and Dock, which macOS starts at login.
        private static func sessionStart() -> Date? {
            ["com.apple.finder", "com.apple.dock"]
                .flatMap { NSRunningApplication.runningApplications(withBundleIdentifier: $0) }
                .compactMap { processStart($0.processIdentifier) }
                .min()
        }

        /// A process's start time (`KERN_PROC_PID`, which the sandbox allows).
        private static func processStart(_ pid: pid_t) -> Date? {
            var info = kinfo_proc()
            var size = MemoryLayout<kinfo_proc>.stride
            var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
            guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0, size > 0 else { return nil }
            let start = info.kp_proc.p_un.__p_starttime
            return Date(timeIntervalSince1970: TimeInterval(start.tv_sec) + TimeInterval(start.tv_usec) / 1_000_000)
        }
    }
#endif
