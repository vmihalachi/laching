# Copilot instructions for webling

All agent guidance lives in [`AGENTS.md`](../AGENTS.md) at the repo root. Read it before
changing code. It isn't duplicated here.

The rules that matter most, for tools that only read this file (such as code review):

- Generic, nothing Adjudeca-specific: names, cookies, paths and secrets come from the app or
  configuration.
- Jobs SQL keeps the names `ApplyWeblingJobs` maps; renaming a column or index breaks every
  app's migration history.
- Every public type has a doc comment saying what it does, what it needs and what it never does.
- Every behavior change comes with a test; tests use real PostgreSQL or the network-free fakes
  already here, never live Cloudflare or Azure.
- Every change gets a line under *Unreleased* in `CHANGELOG.md`; on `0.x` a breaking public API
  change is a minor version.
- C# style is `.editorconfig`, enforced by `dotnet format --verify-no-changes`; warnings are
  errors; package versions live only in `Directory.Packages.props`.
