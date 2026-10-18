# AGENTS.md — Webling

Guide for coding agents (Claude Code reads it through `CLAUDE.md`). Humans start at `README.md`.

**What it is:** MIT NuGet packages with the infrastructure of small ASP.NET Core and Blazor sites on Azure
Container Apps behind Cloudflare; the web sibling of Deskling. Extracted from Adjudeca, which uses the published
packages. Owned by Vlad Mihalachi and changed whenever Adjudeca needs; there is no support for outside users.

## Layout

| Path | Contents |
|---|---|
| `src/Webling.Jobs/` | The PostgreSQL job table: `Job`, `ApplyWeblingJobs`, `EnqueueJob`, `JobDispatcher<TContext>`, `JobCleanup<TContext>`, options and registration. |
| `src/Webling.AspNetCore/` | `CloudflareOrigin`, `SecurityHeaders`, `PublicCaching`, `PublicPageAttribute`. |
| `src/Webling.Cloudflare/` | `TurnstileCheck` / `IHumanCheck`, `CloudflareCache`, registration. HttpClient only: no ASP.NET Core. |
| `src/Webling.Aspire/` | `CloudflareIngress` (with the bundled `cloudflare-ips-v4.txt`) and `ContainerAppJobDeployment`. |
| `src/Webling.Tailwind/` | Build only: `build/Webling.Tailwind.props` and `.targets`, no assembly. |
| `src/Webling.Images/` | `ImageSignature`, `DocumentSignature`, `UploadStream`, `ImageEncoder`. |
| `tests/Webling.<Name>.Tests/` | xUnit, one project per package. Jobs tests run against PostgreSQL; AspNetCore tests on TestServer; Cloudflare tests with a fake handler; Images tests draw their samples in code. |
| `tests/Webling.Tailwind.Sample/` | A web project built by `scripts/tailwind-sample.sh` against the packed Tailwind package. Never in the solution. |
| `site/` | The GitHub Pages site (static HTML, no build), published by `pages.yml`. |
| `scripts/` | `verify.sh` (versions, formatting, tests, Tailwind sample; `--fast` for the first two), `tailwind-sample.sh`. |
| `.github/workflows/` | `ci.yml` (tests with a postgres service, the Tailwind sample on three OSes, a weekly Cloudflare range check), `release.yml`, `pages.yml`. |

## Commands

```sh
dotnet build
dotnet test                       # Jobs tests: WEBLING_TEST_POSTGRES, or localhost:5432 postgres/postgres
dotnet format --verify-no-changes
scripts/tailwind-sample.sh
scripts/verify.sh
```

A Stop hook runs `scripts/verify.sh --fast` and `dotnet format --verify-no-changes` on the files you edited
(`.claude/hooks/validate.json` says which); fix what it reports before ending.

In a cloud container: `sudo pg_ctlcluster 16 main start` (and set the `postgres` user's password to `postgres`), or
`docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`.

## Conventions

- **Generic, not configurable for its own sake.** Nothing Adjudeca-specific: names, cookies, paths and secrets come
  from the app or configuration. Keep the behavior Adjudeca's ADRs describe (016 jobs, 017 Tailwind, 020 Cloudflare
  origin and ingress, 024/028/040 images, 034 caching, 035 Turnstile) unless the owner asks for a change.
- **Jobs SQL uses the names `ApplyWeblingJobs` maps.** Changing a column or index name breaks every app's migration
  history; don't.
- **Every public type has a doc comment** saying what it does, what it needs and what it never does.
- **Tests** cover each package's promises; add one with every behavior change. Tests may use real PostgreSQL and the
  network-free fakes already here, never live Cloudflare or Azure.
- **Tailwind:** the version and the SHA-256 list live in `build/Webling.Tailwind.targets`; an upgrade changes both
  and is a release.
- **Cloudflare ranges:** when `ci.yml`'s weekly check fails, update `src/Webling.Aspire/cloudflare-ips-v4.txt` and
  release, before an app's deployment fails on it.
- **Changelog:** every change gets a line under *Unreleased* in `CHANGELOG.md`. SemVer, `0.x`: a breaking public API
  change is a minor version.
- **Releasing X.Y.Z:** set the `WeblingVersion` default in `Directory.Build.props` and, in `site/index.html`, the
  `vX.Y.Z` label and both `Version="X.Y.Z"` snippets; move *Unreleased* to `## X.Y.Z - YYYY-MM-DD`; run
  `scripts/verify.sh`; commit as `X.Y.Z: <headline change>`. Ask the owner before tagging: `git tag -a vX.Y.Z -m
  "Webling X.Y.Z" && git push origin vX.Y.Z`, or dispatch `release.yml` with the version. Tags never move.
- C# style is `.editorconfig`; `dotnet format` enforces it in CI. Warnings are errors. Central package versions in
  `Directory.Packages.props`.

## Sessions

One task per session: start a new one for unrelated work and hand off follow-ups instead of continuing here. Before searching for code you don't know the location of, use the `context-explorer` subagent (or `jbcontext search`) so the reads stay out of this context. Read big files by outline (`grep -n '^\(func\|class\|struct\|extension\|public\|##\)'`) and then a range, never whole. `/compact` at a milestone in a long session.
