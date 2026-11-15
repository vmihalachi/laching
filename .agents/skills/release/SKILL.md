---
name: release
description: Use when a Laching release (vX.Y.Z) is wanted, when checking what the next one would be or ship, or when moving the apps to a release. Releases are made on demand by dispatching release.yml (dry_run first); a change never bumps the version or tags. Covers the plan, the `breaking` label, when NuGet and npm ship, the checks, and moving the apps.
---

# Releases

The versioning rule (one tag per release, which sides get a new version, no changelog file) is in
`docs/conventions.md`, under *Versioning*. Nothing releases on its own: a change only merges, with a title that
says what changed for an app and the label **`breaking`** when it breaks an API or an app's build; it never bumps the
version or tags.

## Cut one

1. Dispatch **`release.yml`** (Actions → Release → Run workflow, on `main`) with **`dry_run`** checked. Its summary
   prints the plan and the run stops before writing anything:
   - `Plan: release X.Y.Z (why)` (`scripts/release.sh plan`): the next minor while `0.x` (major from 1.0) when a pull
     request merged since the last tag is labelled `breaking`, else the next patch; `Nothing to release` when nothing
     was committed since the tag;
   - `NuGet: ship|skip (why)` (`scripts/release.sh nuget X.Y.Z`);
   - `Web packages: ship|skip (why)` (`scripts/release.sh web X.Y.Z`);
   - `@laching/landing: yes|no (why)` (`scripts/release.sh landing`).
2. **When the release touches `dotnet/` or `tools/windows`**, try it in an app first: the release run tests and packs
   the packages but builds no WinUI app and runs no VM script, and a pushed tag is spent. This lists what it touches:

   ```sh
   git fetch --tags
   git diff --name-only "$(git describe --tags --abbrev=0 origin/main)" origin/main -- dotnet tools/windows
   ```

   When it lists a file, in a worktree or clone of one app made for this, beside this checkout so that its `../laching`
   is this one (up to date with `origin/main`), its `laching` submodule initialised: never a checkout other sessions
   share, since `link` rewrites its pins until `unlink` and the smoke builds and packs from it. In that app:

   ```sh
   python3 laching/tools/companion.py link   # its Windows projects build this checkout's dotnet/
   ../laching/tools/windows/vm.sh smoke      # this checkout's VM scripts: the MSIX published and launched in the VM
   python3 laching/tools/companion.py unlink
   ```

   Dispatch without `dry_run` only once the smoke passed; a failure is fixed on `main` first. No VM on this Mac: leave
   the release to a machine that has one rather than tag untried. There is no hosted stand-in for the smoke yet: an
   app's Windows workflow on GitHub's runners builds the Laching release it pins, never this checkout.
3. Dispatch it again without `dry_run`. The `bump` input (default `auto`) forces `patch`, `minor` or `major`. The
   dispatch is the only human gate: nothing waits for an approval after it.

Nothing remote is written until everything that ships has been tested and packed. The plan job bumps in its runner and
runs `scripts/verify.sh --version`; when NuGet ships, the core and windows jobs test and pack the six packages at X.Y.Z
from that same bumped tree, when the web packages ship the web job tests (against a postgres service) and packs them,
and the landing job packs `@laching/landing`. On every release the swift, kotlin and tools jobs run `swift.yml`,
`kotlin.yml` (the vectors check included) and `tools.yml` (the tools tests) on the planned commit, and the tag waits for
all three green. Only then does the
tag job push the `X.Y.Z: release` commit and its `vX.Y.Z` tag to `main`, in one atomic push onto the commit that was
planned (if `main` moved meanwhile it pushes nothing: dispatch again), and start the site's deploy. A red test or a
refused push therefore spends no tag. After the tag, `npm` publishes the landing and `publish` pushes the packages
through Trusted Publishing and makes the GitHub release: GitHub's notes from the merged pull requests' titles
(*Breaking* first, `.github/release.yml`) and a *Packages* list of what shipped. If either fails, re-run that job.

When packages were pushed, `publish` ends by waiting until NuGet.org lists every one of them at X.Y.Z
(`tools/companion.py nuget-listed`, asking every 15 seconds for up to 15 minutes; it has taken 5 to 7), so **a green
run means the apps can bump**. When that step gives up the run is red although the release is out (the tag, the
push and the GitHub release are done): look on nuget.org at what holds the packages, bump no app meanwhile, and re-run
the job once they are listed (the push skips what is already there).

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
  last shipped in. They ship, all six at X.Y.Z, when `dotnet/Directory.Build.props`, `dotnet/Directory.Build.targets` or
  a packable project's folder changed since `v<LachingVersion>`; the tests, `Fixture.App` and the READMEs (the root one
  and `dotnet/README.md`, the one they pack) don't count, so an edit to `dotnet/README.md` alone reaches nuget.org with
  the next release that ships NuGet for another reason. `bump` writes X.Y.Z into
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
fails after its tag is spent, so fix `main` and release the next number.

## Move the apps

Once the Release run is green, from this checkout:

```sh
tools/bump-apps.sh X.Y.Z   # companion.py bump in every app next to this checkout, then its verify.sh --fast
```

Run it in the background: it ends when the slowest app's pure suites do. The apps' report it ends with places the
release by this checkout's tags, so a vX.Y.Z the Release run tagged on GitHub that this checkout hasn't fetched yet is
fetched from its origin first; one the origin hasn't either refuses the run (exit 2) before any app moves. Each moved
app's pure suites (`swift test`, the core's `dotnet test`, Gradle's core tests: those this machine has the tools for)
start in the background as it moves, beside its `verify.sh --fast`, and their logs are kept when one fails. Then the
apps' report (`companion.py apps`): an app behind this checkout's newest tag, linked, or whose pins disagree fails the
run, as do the apps on different releases. Last it prints, per app, the command that runs its slow checks
(`scripts/verify.sh` without `--fast`): start those in the background too before committing. One run moves an app at a
time: a second one on an app whose lock (`laching-bump-apps.lock` in its Git folder) a live run holds exits 2 at once,
naming that run.

There is no waiting for NuGet by hand: the green run has seen NuGet.org list the packages, and `companion.py bump`
asks it again before it writes anything, for the packages the app pins. While one is missing it says what it is waiting
for; after 15 minutes, or at once when NuGet.org can't be asked (offline), it stops with the app untouched.
`bump-apps.sh` makes that wait once, for every app, before any moves. Run it again later, or with `--no-wait`
(`tools/bump-apps.sh --no-wait X.Y.Z`) to pin unasked. NuGet's http-cache needs
clearing (`dotnet nuget locals http-cache --clear`, on the machine that restored) only where a restore failed before
NuGet.org listed the version: after `--no-wait`, or after a `bump` run from an app's own submodule while that still
holds a release older than this check (`bump-apps.sh` runs this checkout's `companion.py`, so it always asks).

`companion.py bump` pins every `Laching.*` NuGet reference at the release's `LachingVersion` (X.Y.Z or earlier) and
everything else at the tag. It commits nothing: read each app's diff, build what the release touched, and commit there
(the landing page's `@laching/landing` change inside its own repository first). A change that spans Laching and the
apps is tried before the release with `companion.py link` in each app (`tools/README.md`, *Moving to a release, or
trying one first*).
