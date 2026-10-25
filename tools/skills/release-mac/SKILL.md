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
update the review notes, push the release commits and tag, and run the hosted smoke workflow without stopping to ask.
Read each dry run and stop only when it shows something unexpected (another platform's version, screenshots being
replaced, a version in an unexpected state). Still the owner's: new store screenshots (ask first), credentials (never
handle them), and submitting for review. When another session's unpushed commits sit on top, push only the release's
own: `git push origin <sha>:refs/heads/master`.

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
share the App Store copy's data). When the app's notes name a hosted Release smoke workflow, start it on GitHub's
runners (`gh workflow run <workflow> -f hosted=true`; it bills macOS minutes, so only for a release; push the release
commit first) and make sure it's green for the commit you archive.

Also confirm: every user-visible Mac change is ported to Windows (or says why not), README, landing and store-listing
claims match what ships, and purchase UI is only where the app's rules allow.

## 3. Bump, commit, archive, upload

1. Edit the version in the XcodeGen spec, run `xcodegen generate` in `mac.dir`, commit the spec and the regenerated
   project with the work (message like `Release 1.0 (4)`).
2. Archive (Release signs automatically with the team in the notes; Debug stays ad-hoc):
   ```sh
   cd <mac.dir>
   xcodebuild archive -project <mac.project> -scheme <mac.scheme> -configuration Release \
     -destination 'generic/platform=macOS' -archivePath build/release.noindex/<mac.scheme>.xcarchive -allowProvisioningUpdates
   ```
3. Upload with an export-options plist (method `app-store-connect`, destination `upload`, the team ID, signingStyle
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
3. **Push** once the build has processed (the email, or the TestFlight tab):
   `python3 <store.dir>/apple/push.py --version <v> --create --build <n> --only text,iap,screenshots,review` prints the
   plan (drop `screenshots` when step 2 was skipped, `iap` when the in-app purchases' text didn't change); read it,
   then rerun with `--apply`. In a universal app always pass `--only` with the Mac parts: the default also
   pushes the iOS page, under the Mac's version number. It creates the version with the release type of
   `store.apple.releaseType` (an existing editable version is set to it too), attaches the build, and syncs every
   language's text, the in-app purchases' text and the screenshots. `--status --version <v>` reads what App Store
   Connect has and still needs. Without an API key (`ASC_KEY_ID`, `ASC_ISSUER_ID`, the `.p8`), the owner pastes by
   hand from the same files; never handle the key yourself.
4. **Review notes** (`store.apple.review.notes`): update them when behavior changed (the `review` part pushes them);
   reply in the Resolution Center thread when App Review asked something. The in-app purchases go in the submission
   when they're new or their text changed; a new one must be Ready to Submit.
5. **Before submitting** (the owner), install the processed build from TestFlight and check it the way review will:
   it launches, onboarding opens on a fresh install, the main windows open, purchases show their prices (and anything
   the app's notes add). A build that fails here never goes to review: fix it and upload a new build number.
6. **Submit** (the owner: **Add for Review**, or **Resubmit**). The release follows `store.apple.releaseType`:
   `AFTER_APPROVAL` goes live on its own once Apple approves; `MANUAL` (or unset) waits for the owner's **Release**.
   Change it in `companion.json`, never by hand in App Store Connect, so the next push doesn't flip it back.

## 5. Wrap up

- Tag `mac-<version>-<build>` (for example `mac-1.0-4`) on the release commit and push the release's commits and the
  tag (only the release's own, see *Go-ahead*).
- Record the build number and review status where the app's notes say.
