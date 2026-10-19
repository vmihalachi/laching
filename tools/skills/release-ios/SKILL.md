---
name: release-ios
description: Ship a new build of a Laching companion's iPhone and iPad app to App Store Connect (bump the iOS CFBundleVersion, verify, archive, upload, the iOS version page's text, screenshots (iPhone, iPad, iPhone Duo) and iOS 27 product page header and search results asset through push.py, the TestFlight check, submit, tag). Use when asked to publish, release, upload or submit the iOS, iPhone or iPad app, or a new build of it.
---

# Release the iPhone and iPad app (App Store)

For a universal app (`store.apple.ios` in `companion.json`): the same App Store Connect record as the Mac app (one
bundle ID, one purchase), with its own version page, version number and build numbers. Releasing the Mac app is the
`release-mac` skill; the two never share a version number, so never push one platform's parts with the other's version.

Names come from `companion.json` (`mac.dir`, `mac.project`, `mac.iosScheme`, `mac.projectSpec`, `store.dir`,
`store.apple`); the team ID, the in-app purchase ids, the iOS target's place in the spec, the app's own checks and where
the build log and review status are kept are in the app's `.agents/notes/release-ios.md` (and what it shares with the
Mac in `release-mac.md`). Read them first, and check the App Review thread before starting a new build for a version
in review.

## Go-ahead

Running this skill is the owner's go-ahead for the whole release: upload, apply the store push (`push.py --apply`),
update the review notes, push the release commits and tag, and run the hosted smoke workflow without stopping to ask.
Read each dry run and stop only when it shows something unexpected (another platform's version, screenshots being
replaced, a version in an unexpected state). Still the owner's: new store screenshots (ask first), credentials (never
handle them), and submitting for review. When another session's unpushed commits sit on top, push only the release's
own: `git push origin <sha>:refs/heads/master`.

## 1. Decide the version

- The iOS target's `CFBundleShortVersionString` and `CFBundleVersion` are in its own `info` → `properties` in the
  XcodeGen spec, not the Mac target's. Never hand-edit `project.pbxproj`.
- **New build of the same version** (update while in review or rejected): raise `CFBundleVersion` by 1.
- **New version** (after approval): bump `CFBundleShortVersionString` too; `CFBundleVersion` only goes up, never
  resets.

## 2. Preflight (don't upload a build that fails these)

Follow the app's shared-checkout rules (`AGENTS.md`): stage only your own paths.

```sh
scripts/verify.sh --all
cd <mac.dir> && xcodebuild -project <mac.project> -scheme <mac.iosScheme> -destination '<an iOS Simulator>' test
python3 laching/tools/l10n.py check
```

The Mac's tests and the conformance vectors too when the code is shared (the app's notes say how). Then run the
Release app once on a simulator or a device the way review will: it launches, onboarding opens on a fresh install, the
main screens open, anything that needs a permission asks only when the feature is chosen, purchases show their prices.
Check the iPad too (both orientations, Split View). Confirm README, landing and store-listing claims about iPhone and
iPad match what ships, and that purchase UI is only where the app's rules allow.

## 3. Bump, commit, archive, upload

1. Edit the iOS version in the XcodeGen spec, run `xcodegen generate` in `mac.dir`, commit the spec and the regenerated
   project with the work (`Release iOS 1.0 (2)`).
2. Archive (Release signs automatically with the team in the notes):
   ```sh
   cd <mac.dir>
   xcodebuild archive -project <mac.project> -scheme <mac.iosScheme> -configuration Release \
     -destination 'generic/platform=iOS' -archivePath build/release.noindex/<mac.iosScheme>.xcarchive -allowProvisioningUpdates
   ```
3. Upload with an export-options plist (method `app-store-connect`, destination `upload`, the team ID, signingStyle
   `automatic`):
   ```sh
   xcodebuild -exportArchive -archivePath build/release.noindex/<mac.iosScheme>.xcarchive \
     -exportOptionsPlist build/release.noindex/ExportOptions.plist -exportPath build/release.noindex/ios -allowProvisioningUpdates
   ```
   This needs the owner's Apple ID signed in to Xcode. If it fails on auth, stop and ask; never handle credentials.

## 4. App Store Connect

1. **What's New:** the platform's first version has none. For every later one, rewrite `ios.whatsnew` in every
   `<store.dir>/<lang>.md` (the source language first, only what ships on iPhone and iPad since the last approved iOS
   build), then `python3 <store.dir>/check.py`. Commit it.
2. **Screenshots: skip by default**, as for the Mac: render new ones only when a screen they show changed visibly. When
   unsure, ask the owner. Render the source language first (all three devices; the app's notes name the command), and
   every language (slow) once the owner has OK'd them. The devices: iPhone 6.9" (1320×2868, `store.apple.ios.iphoneScreenshots`),
   iPad 13" (2064×2752, `ios.ipadScreenshots`) and **iPhone Duo** (1398×2034 or 2007×2853; the `iPhone-Duo`
   simulator captures at either scale; `ios.duoScreenshots`, `<store.dir>/apple/duo/<lang>/` by default). Smaller
   iPhones and iPads scale from these; the Duo doesn't, so without its own set its tab stays empty. App Store Connect
   has no screenshot set for the Duo: push.py places them in the App Asset Library, in file order, when the folder
   exists.
3. **Product page header and search results asset** (iOS 27; no text, so one image each serves every language; no
   alpha): regenerate only when the art changes (the app's notes say how). No prices, URLs, logos or other platforms.
   - **Header**: `store.apple.ios.header`, `<store.dir>/apple/header.png` by default, 3840×1646. Keep the subject inside
     the middle 1646×661 (the safe area: x 1097–2743, y 493–1154).
   - **Search results**: `store.apple.ios.searchResults`, `<store.dir>/apple/search-results.png` by default, 3:2 from
     1920×1280 to 3840×2560 (3840×2560 is best), or 5244×2950. It shows in App Store search results in place of the
     screenshots (the app's name and icon sit beside it); without it, search shows the screenshots.
4. **Push** once the build has processed:
   `python3 <store.dir>/apple/push.py --version <iOS version> --create --build <n> --only ios-text,ios-screenshots,ios-header,ios-search,review`
   prints the plan (drop `ios-screenshots`, `ios-header` or `ios-search` when they didn't change; `ios-screenshots`
   covers the Duo's too); read it, then rerun with `--apply`. Always pass `--only` with the iOS parts:
   without them the Mac's page is pushed too, under the iOS version number. It creates the iOS version with the
   release type of `store.apple.releaseType` (an existing editable version is set to it too), attaches the build, and
   syncs every language's text, the screenshots, the header (uploaded once to the app's Asset Library, placed on
   every language; a changed file replaces it) and the review notes. The search results asset is placed like the
   header. Add `iap` when the in-app purchases' text changed. `--status --version <v>` reads what App Store Connect
   has and still needs (iPhone Duo screenshots and header and search results placements per language included).
   Without an API key (`ASC_KEY_ID`, `ASC_ISSUER_ID`, the `.p8`), the owner pastes by hand from the same files; never
   handle the key yourself.
5. **The first iOS version** also needs, once, in App Store Connect (the owner): the iOS platform added to the app,
   the iPhone and iPad privacy answers (the same as the Mac's), and the age rating confirmed. The in-app purchases go
   in the first iOS submission.
6. **Before submitting** (the owner), install the processed build from TestFlight on an iPhone and an iPad and run the
   checks from step 2. A build that fails here never goes to review: fix it and upload a new build number.
7. **Submit** (the owner: **Add for Review**, or **Resubmit**). The release follows `store.apple.releaseType`:
   `AFTER_APPROVAL` goes live on its own once Apple approves; `MANUAL` (or unset) waits for the owner's **Release**.
   Change it in `companion.json`, never by hand in App Store Connect, so the next push doesn't flip it back.

## 5. Wrap up

- Tag `ios-<version>-<build>` (for example `ios-1.0.0-1`) on the release commit and push the release's commits and
  the tag (only the release's own, see *Go-ahead*).
- Record the iOS build number and review status where the app's notes say.
