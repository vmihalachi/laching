# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

The project site: hand-written static HTML/CSS in `site/`, deployed to GitHub Pages by a `pages.yml` workflow. No build
tools and no dependencies, matching the package's own no-dependency rule.

## Users

Indie developers building a small app that lives in the macOS menu bar, the Windows tray or a phone, in Swift, .NET or Kotlin,
evaluating whether to adopt Laching instead of writing the same plumbing again, and those building a desk companion
(a character that talks on a cadence and knows when to be quiet). Secondary: the maintainers of mybackhurts,
sunnysays and Mister Pagliacci, who use it as a reference.

## Product Purpose

Laching is an MIT Swift package plus .NET packages and a Kotlin build with the generic parts every menu bar, tray or
phone app needs: a pure reminder scheduler, permission-free system signals, app-shell plumbing, a purchase store
(StoreKit 2, the Microsoft Store and Google Play) and, since 0.5.0, the companion layer: phrases, cadence, quiet rules,
settings, skins, character rigs, speech and the tooling a companion app needs. Beside them, and sharing nothing with
them, the `Laching.Web` packages for small ASP.NET Core sites behind Cloudflare. Success for the site: a
visitor understands what is inside, trusts the permission-free and cross-platform promises, and copies the install line.

## Positioning

- Call detection (camera or microphone in use by another app) read from device state, never by opening a device, so it
  never prompts for a permission. Nothing in it touches the network.
- One behavior on every platform: the Swift code is the reference, JSON conformance vectors are generated from it, the
  C# port replays every one and the Kotlin port the scheduler's.
- Extracted from two real apps (mybackhurts, which ships it on macOS, iPhone and iPad and Windows, with Android in
  development, and sunnysays, in development), not designed in the abstract. The companion layer is sunnysays' character
  skeleton made generic; Mister Pagliacci (in development, Mac and Windows) is built on it.

## Capabilities and Constraints

Products: `LachingCore` / `Laching.Core` (scheduler, clock/idle/busy protocols, SplitMix64; the drawing math in Swift
and Kotlin only),
`LachingCompanion` / `Laching.Companion` (a desk companion's phrases, cadence, quiet rules, settings, coordinator,
desktop presence, skins and vector or raster character rigs), `LachingSystem` / `Laching.Windows` (idle, busy, full-screen, power, audio output, screen lock, appearance),
`LachingShell` / `Laching.Windows` (window manager, login item, notifications, tray icon, single
instance; on the Mac also the status item, speech and chimes), `LachingCompanionUI` / `Laching.Companion.Shell` + `Laching.Windows.Companion` (the
companion's views, desktop presence and delivery), `LachingStore` / `Laching.Core` `Store/` + `Laching.Windows` (one-time unlockables and tips: StoreKit 2 on the Mac,
the Microsoft Store on Windows, both since 0.3.0), `LachingTesting` / `Laching.Testing` (fakes, fixtures, conformance generators and replays), the Kotlin modules for
Android (`laching.core`, the scheduler, store and drawing; `laching.android`, Play Billing, alarms and notifications;
`laching.testing`, the fakes), the six `Laching.Web.*` packages (jobs, the Cloudflare origin check and headers,
Turnstile, Aspire ingress, Tailwind, images), and `tools/` (Python tools, scripts and agent skills a companion app
vendors as a submodule).
macOS 14+, iOS 17+, Windows 11 (.NET 10) and Android 8+ (Kotlin); menu bar, tray and phone. Version 0.x: minor versions may change public types. The package ships no strings.

## Brand Commitments

Name: Laching. Voice: plain, precise, dry, first-hand ("the parts every such app needs and nobody wants to write
twice"). The logo is `site/favicon.svg`: a dark rounded square with a white bar and a teal dot.

## Evidence on Hand

README, the GitHub releases' notes (every release; up to 0.15.0 also the CHANGELOG.md in the history), the scheduler
conformance vectors in `conformance/scheduler/` and the companion vectors in `conformance/companion/`,
mybackhurts.app, sunnysays.app, pagliacci.app.
No testimonials, download counts, benchmarks or third-party adopters: never invent them.

## Product Principles

- Never prompt, never phone home.
- Behavior is specified once and proven on every platform.
- The app owns its words; the package owns the plumbing.
