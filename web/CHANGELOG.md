# Changelog

All notable changes to Webling. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/). One tag (`vX.Y.Z`) versions every package; every change
gets a line under *Unreleased*.

## Unreleased

## 0.1.0 - 2026-10-09

The first release: the self-contained infrastructure of Adjudeca, extracted and made generic.

### Added
- `Webling.Jobs`: the job table, `EnqueueJob`, `IJobHandler<T>` and the dispatcher from Adjudeca (ADR-016), with
  the same lease, backoff, attempt and shutdown behavior. Generic over the app's context:
  `AddJobDispatcher<TContext>()`, `EnqueueJob` on any `DbContext`, and `ApplyWeblingJobs()`, which maps the `jobs`
  table with explicit snake_case names. New: done jobs are deleted after `KeepDone` (7 days) and failed jobs after
  `KeepFailed` (30 days), every `CleanupInterval` (1 hour).
- `Webling.AspNetCore`: `AddCloudflareOrigin` / `UseCloudflareOrigin` (health paths now an option),
  `UseSecurityHeaders` with a `reportOnly` switch and `GetCspNonce`, `PublicCaching` with the personal cookies from
  `AddPublicCaching`, and `[PublicPage]`.
- `Webling.Cloudflare`: `TurnstileCheck` behind `IHumanCheck`, `CloudflareCache`, `AddTurnstile()` and
  `AddCloudflareCache()`; the Turnstile test keys as constants.
- `Webling.Aspire`: `UseCloudflareIngress()` on a Container App, the bundled Cloudflare IPv4 list with
  `LoadRanges`, `ParseRanges` and `EnsureRangesCurrentAsync`, and `RunJobBeforeApps()` with the job name as a
  parameter.
- `Webling.Tailwind`: the Tailwind 4.3.3 build targets as `build/` props and targets, downloading into
  `~/.webling/tailwindcss/<version>/`.
- `Webling.Images`: `ImageSignature`, `DocumentSignature`, `UploadStream` (now public) and `ImageEncoder`.
- Tests for every package (the job queue against PostgreSQL), a Tailwind sample built on Linux, Windows and macOS,
  and a weekly check of the bundled Cloudflare ranges.
