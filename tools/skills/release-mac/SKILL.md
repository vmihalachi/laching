---
name: release-mac
description: Ship a new build of a Laching companion's Mac app to App Store Connect (bump CFBundleVersion, verify, archive, upload, What's New and the listing through push.py, the TestFlight check, submit, tag). Use when asked to publish, release, upload or submit a new macOS version or build, including a new build for a version already in review.
---

# Release the Mac app (App Store)

Names come from `companion.json` (`mac.dir`, `mac.project`, `mac.scheme`, `mac.projectSpec`, `store.dir`,
`store.apple`); the team ID, the App Store Connect record, the in-app purchase ids, the app's own checks and where the
build log and review status are kept are in the app's `.agents/notes/release-mac.md`. Read it first, and check the
App Review thread in App Store Connect before starting a new build for a version in review.

Distribution is the Mac App Store only (and TestFlight from the same upload): no DMG, no notarization. A universal app
(`store.apple.ios` set) has an iPhone and iPad version with its own version number: that one is the `release-ios`
skill, and the two never share a version number.

## Go-ahead

Running this skill is the owner's go-ahead for the whole release: upload, apply the store push (`push.py --apply`),
update the review notes, push the release commits and tag, and start the release gate's run
(`release-gate.py --dispatch`, the Gate step of section 3) without stopping to ask. Read each dry run and stop only when
it shows something unexpected (another platform's version, screenshots being replaced, a version in an unexpected
state); a gate that refuses stops the release until it passes, never to be worked around. Still the owner's: new store
screenshots (ask first), credentials (never handle them), and submitting for review. When another session's unpushed
commits sit on top, push only the release's own: `git push origin <sha>:refs/heads/main`.

## 1. Decide the version

- **New build of the same version** (update while in review or rejected): keep `CFBundleShortVersionString`, raise
  `CFBundleVersion` by 1.
- **New version** (after approval): bump `CFBundleShortVersionString` too; `CFBundleVersion` only goes up, never
  resets.
- Both live in the Mac target of the XcodeGen spec (`mac.dir`/`mac.projectSpec`). Never hand-edit `project.pbxproj`.

## 2. Preflight (don't upload a build that fails these)

Follow the app's shared-checkout rules (`AGENTS.md`): stage only your own paths.

```sh
scripts/verify.sh --all          # includes the Laching pin check (submodule = SwiftPM = XcodeGen; NuGet = its LachingVersion)
laching/tools/conformance.sh && git diff --exit-code <conformance.dir>   # vectors current (or the app's own script, per its notes)
python3 laching/tools/l10n.py check
python3 laching/tools/voice.py check   # when the app has a voice
```

The tests run the Debug app (`mac.devAppName`); App Store review gets Release, which nothing local launches (it would
share the App Store copy's data). The app's workflow that calls `macos-app.yml` runs the whole suite and builds the
Release apps (`release` is on by default; `release: false` skips them), and on GitHub's runners (`hosted: true`)
launches the Mac one (`laching/tools/smoke-release.sh`): the release gate (the Gate step of section 3) wants that run
green on the exact commit you tag.

Also confirm: every user-visible Mac change is ported to Windows (or says why not), README, landing and store-listing
claims match what ships (the `landing-copy` skill, when a platform, store or purchase changed), and purchase UI is only
where the app's rules allow.

## 3. Bump, commit, archive, upload

1. Edit the version in the XcodeGen spec, run `xcodegen generate` in `mac.dir`, commit the spec and the regenerated
   project with the work (message like `Release 1.0 (4)`).
2. **Gate** the release commit: push it (only the release's own, see *Go-ahead*), then
   ```sh
   python3 laching/tools/release-gate.py mac --dispatch   # --commit <sha> when other commits sit on top of it
   ```
   It passes (exit 0) only when the app's `macos-app.yml` caller passed on that exact commit
   (`gh run list --commit <sha>`), with the hosted Release launch when the caller builds Release (it does unless it sets
   `release: false`), and no check the agent hook recorded as skipped (`hook.py skips --json`) covers a path that ships.
   A dispatch runs a branch's tip, so it runs the origin branch whose tip is the commit; when none is (others' commits
   were pushed on top), the gate says how to push it to a branch of its own. With no such run it starts one on GitHub's
   runners (`-f hosted=true`, and `-f release=true` when the caller forwards a `release` input of its own; it bills
   macOS minutes, so only for a release) and exits 3: start the `gh run watch <id> --exit-status` it prints in the
   background, carry on with the archive and upload, and run the gate again when the watch ends. A refusal (exit 1) says
   what clears it: a failed run (fix it in a new commit, or `gh run rerun <id> --failed` for a flake), a skip over a
   path that ships, whatever platform its check tests (an Android replay skipped over `conformance/` holds this release
   too: run that check until it passes, as `scripts/verify.sh` routes it). Archive from a tree with no uncommitted
   shipped changes: the gate names them, since the archive builds the working tree and the run never saw them. When the
   iPhone and iPad app ships from the same commit, gate the Mac first: its hosted run clears the `ios` gate too, while a
   self-hosted run the `ios` gate started first would leave this one to start a second, hosted run.
3. Archive (Release signs automatically with the team in the notes; Debug stays ad-hoc):
   ```sh
   cd <mac.dir>
   xcodebuild archive -project <mac.project> -scheme <mac.scheme> -configuration Release \
     -destination 'generic/platform=macOS' -archivePath build/release.noindex/<mac.scheme>.xcarchive -allowProvisioningUpdates
   ```
4. Upload with an export-options plist (method `app-store-connect`, destination `upload`, the team ID, signingStyle
   `automatic`):
   ```sh
   xcodebuild -exportArchive -archivePath build/release.noindex/<mac.scheme>.xcarchive \
     -exportOptionsPlist build/release.noindex/ExportOptions.plist -exportPath build/release.noindex/out -allowProvisioningUpdates
   ```
   This needs the owner's Apple ID signed in to Xcode. If it fails on auth, stop and ask; never handle credentials.

## 4. App Store Connect

1. **What's New:** for a new version, rewrite `apple.whatsnew` in every `<store.dir>/<lang>.md` (the source language
   first, only what ships since the last approved build; the platform's first version has none), then
   `python3 <store.dir>/check.py`. Commit it.
2. **Screenshots: skip by default.** Render new store screenshots only when a window they show changed visibly enough
   that the listing would look wrong; bug fixes rarely do. When unsure, ask the owner before rendering or uploading any.
   If needed: `laching/tools/screenshots.sh` when windows changed, then `python3 laching/tools/store-screenshots.py`
   (the source language; or the app's own scripts, per its notes); render `--lang all` (every language, slow) once the
   owner has OK'd the source language's. When only some windows changed, add `--only <shot>[,<shot>]` (a window, in
   both appearances, or a capture's full name): just those windows are captured and their images replaced, the others
   kept; a language whose folder lacks the rest stops the run before any capture, to be rendered whole.
3. **Push** right after the upload, without waiting for the email:
   `python3 <store.dir>/apple/push.py --version <v> --create --build <n> --wait-build --only text,iap,screenshots,review`
   waits until App Store Connect has processed the build (it asks every 30 seconds, for up to 30 minutes;
   `--wait-build=45` for longer), so run it in the background, and prints the plan (drop `screenshots` when step 2
   was skipped, `iap` when the in-app purchases' text didn't change); read it, then rerun with `--apply`. A build
   that ends `FAILED` or `INVALID` stops the wait at once: fix it and upload a new build number. When the time is up
   the rest is planned without the build: run the same command again. In a universal app always pass `--only` with
   the Mac parts: the default also pushes the iOS page, under the Mac's version number. It creates the version with
   the release type of
   `store.apple.releaseType` (an existing editable version is set to it too), attaches the build, and syncs every
   language's text, the in-app purchases' text and the screenshots. `--status --version <v>` reads what App Store
   Connect has and still needs. Without an API key (`ASC_KEY_ID`, `ASC_ISSUER_ID`, the `.p8`), the owner pastes by
   hand from the same files; never handle the key yourself.
4. **Review notes** (`store.apple.review.notes`): update them when behavior changed (the `review` part pushes them);
   reply in the Resolution Center thread when App Review asked something. The in-app purchases go in the submission
   when they're new or their text changed; a new one must be Ready to Submit. A changed text is a new version of the
   purchase, live only once reviewed: `--status` names a purchase whose newest text has still to be submitted, and
   the push leaves the text alone while one is with App Review.
5. **Before submitting** (the owner), install the processed build from TestFlight and check it the way review will:
   it launches, onboarding opens on a fresh install, the main windows open, purchases show their prices (and anything
   the app's notes add). A build that fails here never goes to review: fix it and upload a new build number.
6. **Submit** (the owner: **Add for Review**, or **Resubmit**) once `release-gate.py mac` exits 0 for the build's
   commit; while it refuses or waits, tell the owner not to. The release follows `store.apple.releaseType`:
   `AFTER_APPROVAL` goes live on its own once Apple approves; `MANUAL` (or unset) waits for the owner's **Release**.
   Change it in `companion.json`, never by hand in App Store Connect, so the next push doesn't flip it back.

## 5. Wrap up

- Tag `mac-<version>-<build>` (for example `mac-1.0-4`) on the release commit once
  `python3 laching/tools/release-gate.py mac` (with `--commit <sha>` when needed) exits 0, never while it refuses, and
  push the release's commits and the tag (only the release's own, see *Go-ahead*).
- Record the build number and review status where the app's notes say.
