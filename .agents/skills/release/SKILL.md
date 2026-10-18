---
name: release
description: Use when a Laching release (vX.Y.Z) is wanted, when checking what the next one would be or ship, or when moving the apps to a release. Releases are made on demand by dispatching release.yml (dry_run first); a change never bumps the version or tags. Covers the plan, the `breaking` label, when NuGet and npm ship, the checks, and moving the apps.
---

# Releases

One tag per release. Swift and Kotlin take the tag; NuGet and npm get a new version only when their code changed since
their last one. There is no changelog file and nothing releases on its own: a change only merges, with a title that
says what changed for an app and the label **`breaking`** when it breaks an API or an app's build; it never bumps the
version or tags.

## Cut one

1. Dispatch **`release.yml`** (Actions → Release → Run workflow, on `master`) with **`dry_run`** checked. Its summary
   prints the plan and the run stops before writing anything:
   - `Plan: release X.Y.Z (why)` (`scripts/release.sh plan`): the next minor while `0.x` (major from 1.0) when a pull
     request merged since the last tag is labelled `breaking`, else the next patch; `Nothing to release` when nothing
     was committed since the tag;
   - `NuGet: ship|skip (why)` (`scripts/release.sh nuget X.Y.Z`);
   - `Web packages: ship|skip (why)` (`scripts/release.sh web X.Y.Z`);
   - `@laching/landing: yes|no (why)` (`scripts/release.sh landing`).
2. Dispatch it again without `dry_run`. The `bump` input (default `auto`) forces `patch`, `minor` or `major`. The
   dispatch is the only human gate: nothing waits for an approval after it.

Nothing remote is written until everything that ships has been tested and packed. The plan job bumps in its runner and
runs `scripts/verify.sh --version`; when NuGet ships, the core and windows jobs test and pack the six packages at X.Y.Z
from that same bumped tree, when the web packages ship the web job tests (against a postgres service) and packs them,
and the landing job packs `@laching/landing`. Only then does the tag job push the
`X.Y.Z: release` commit and its `vX.Y.Z` tag to `master`, in one atomic push onto the commit that was planned (if
`master` moved meanwhile it pushes nothing: dispatch again), and start the site's deploy. A red test or a refused push
therefore spends no tag. After the tag, `npm` publishes the landing and `publish` pushes the packages through Trusted
Publishing and makes the GitHub release: GitHub's notes from the merged pull requests' titles (*Breaking* first,
`.github/release.yml`) and a *Packages* list of what shipped. If either fails, re-run that job.

By hand, from a checkout with its tags (`git fetch --tags`; `plan` reads the labels with `gh`, or takes
`--bump patch|minor|major`):

```sh
scripts/release.sh plan            # release 0.15.1 (patch: none of the 2 pull requests merged is labelled breaking; …)
scripts/release.sh nuget 0.15.1    # ship|skip (why)
scripts/release.sh web 0.15.1      # ship|skip (why)
scripts/release.sh landing         # yes|no (why)
```

## When NuGet and npm ship

- **NuGet**: the six packages share one version, `LachingVersion` in `dotnet/Directory.Build.props`: the release they
  last shipped in. They ship, all six at X.Y.Z, when `dotnet/Directory.Build.props` or a packable project's folder
  changed since `v<LachingVersion>`; the tests, `Fixture.App` and the README don't count. `bump` writes X.Y.Z into
  `LachingVersion` only then, in the tagged release commit, so the next diff from that tag starts clean.
- **`Laching.Web.*`**: the same, on their own `LachingWebVersion` (`web/Directory.Build.props`): all six at X.Y.Z when
  `web/src`, `web/Directory.Build.props`, `web/Directory.Build.targets` or `web/Directory.Packages.props` changed since
  `v<LachingWebVersion>`. Same Trusted Publishing policy and push as the others.
- **`@laching/landing`**: when `tools/landing` changed since the version on npm, at the release's version.

## The version strings

`bump` writes them; `scripts/verify.sh --version X.Y.Z` gates the release on them before anything is committed, and
plain `scripts/verify.sh` checks they agree:

| File | What |
|---|---|
| `Sources/LachingCore/Laching.swift` | `public static let version = "X.Y.Z"` (SwiftPM has no build-time constant) |
| `kotlin/gradle.properties` | `lachingVersion=X.Y.Z`; `:core` generates `Laching.VERSION` from it |
| `site/index.html` | the `vX.Y.Z` label in the header and **both** `exact: "X.Y.Z"` install snippets |
| `README.md` | the `exact: "X.Y.Z"` install pin and "the latest release is `vX.Y.Z`" under *Releases* |
| `dotnet/Directory.Build.props` | `LachingVersion`: NuGet's, at or below the tag; `LachingInfo.Version` is generated from it |
| `web/Directory.Build.props` | `LachingWebVersion`: the web packages', at or below the tag |

Tags are protected and never move: the run stops before writing anything when `vX.Y.Z` exists, and a release that
fails after its tag is spent, so fix `master` and release the next number.

## Move the apps

Once NuGet lists the packages (when they shipped: nuget.org indexes them a few minutes after the run), from this
checkout:

```sh
tools/bump-apps.sh X.Y.Z   # companion.py bump in every app next to this checkout, then its verify.sh --fast
```

`companion.py bump` pins every `Laching.*` NuGet reference at the release's `LachingVersion` (X.Y.Z or earlier) and
everything else at the tag. It commits nothing: read each app's diff, build what the release touched, and commit there
(the landing page's `@laching/landing` change inside its own repository first). A change that spans Laching and the
apps is tried before the release with `companion.py link` in each app (`tools/README.md`, *Moving to a release, or
trying one first*).
