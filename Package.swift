// swift-tools-version: 6.0
import PackageDescription

// Laching: building blocks for little desktop apps that live in the menu bar.
//
//   LachingCore     Foundation only. Interval scheduler with active hours and weekdays, quiet hours, idle reset and
//                    busy hold; the Clock / IdleTimeProvider / BusyStateProvider protocols; a seeded random source;
//                    LocalizedKey; the typed SettingsStore; the drawing math (SVG path sampling, wobble, keyframes,
//                    polylines; Apple platforms). Builds and tests anywhere Swift runs.
//   LachingSystem   The macOS implementations of those providers and the other system signals (camera or microphone
//                    in use, full screen, power source, output mute and route, screen lock, appearance), read from
//                    device state through CoreAudio, CoreMediaIO, CoreGraphics and IOKit, all without permission prompts.
//   LachingShell    Menu bar app plumbing: window manager with the activation-policy dance, the floating desktop
//                    window and its placement, login item, launch context, notification poster, font registration,
//                    a layout check, the status item, and speech (system voice or recorded clips,
//                    jokes in beats) and chimes.
//   LachingCompanion  Foundation and Observation only. The pure core of a desk companion: the phrase bank and
//                    picker, chatter cadence, quiet rules and mood, settings, the coordinator, desktop presence,
//                    skins and the character rig. Builds and tests anywhere Swift runs.
//   LachingCompanionUI  macOS. The companion's views and shell: signals, the menu bar glyph, the character view
//                    (vector and image parts), the desktop presence, delivery through speech, chime and notification,
//                    the speech bubble and status pill over an app palette, screenshot capture, and the app shell an
//                    app subclasses (app model, menus, Settings rows, the Support window).
//   LachingStore    StoreKit 2 purchase store for unlockables and tips, caching ownership in a SettingsStore.
//   LachingTesting  Fakes for every protocol, fixture data and conformance-vector helpers, for the apps' test targets.
let package = Package(
    name: "Laching",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [
        .library(name: "LachingCore", targets: ["LachingCore"]),
        .library(name: "LachingSystem", targets: ["LachingSystem"]),
        .library(name: "LachingShell", targets: ["LachingShell"]),
        .library(name: "LachingCompanion", targets: ["LachingCompanion"]),
        .library(name: "LachingCompanionUI", targets: ["LachingCompanionUI"]),
        .library(name: "LachingStore", targets: ["LachingStore"]),
        .library(name: "LachingTesting", targets: ["LachingTesting"]),
    ],
    targets: [
        .target(name: "LachingCore"),
        .target(name: "LachingSystem", dependencies: ["LachingCore"]),
        .target(name: "LachingShell", dependencies: ["LachingCore"]),
        .target(name: "LachingCompanion", dependencies: ["LachingCore"]),
        .target(
            name: "LachingCompanionUI",
            dependencies: ["LachingCompanion", "LachingCore", "LachingSystem", "LachingShell", "LachingStore"]),
        .target(name: "LachingStore", dependencies: ["LachingCore"]),
        .target(name: "LachingTesting", dependencies: ["LachingCore", "LachingStore", "LachingCompanion"]),
        .testTarget(name: "LachingCoreTests", dependencies: ["LachingCore", "LachingTesting"]),
        .testTarget(
            name: "LachingCompanionTests", dependencies: ["LachingCompanion", "LachingCore", "LachingTesting"],
            resources: [.copy("Resources")]),
        .testTarget(
            name: "LachingTestingTests", dependencies: ["LachingTesting", "LachingCore", "LachingCompanion", "LachingStore"]),
        // The ones below are macOS-only (every file is wrapped in `#if os(macOS)`), so they build empty on Linux.
        .testTarget(name: "LachingSystemTests", dependencies: ["LachingSystem", "LachingCore"]),
        .testTarget(name: "LachingShellTests", dependencies: ["LachingShell", "LachingCore"], resources: [.copy("Resources")]),
        .testTarget(name: "LachingStoreTests", dependencies: ["LachingStore", "LachingTesting"]),
        .testTarget(
            name: "LachingCompanionUITests",
            dependencies: ["LachingCompanionUI", "LachingCompanion", "LachingShell", "LachingCore", "LachingTesting"],
            // Processed, so the PNGs sit at the bundle's top level where `Image(_:bundle:)` finds them.
            resources: [.process("Resources")]),
    ]
)
