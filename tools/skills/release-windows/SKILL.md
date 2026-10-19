---
name: release-windows
description: Ship a new build of a Laching companion's Windows app to the Microsoft Store (bump the package version, build and smoke-test the x64 and ARM64 Native AOT packages, bundle, Store screenshots and listing, the Partner Center submission, the win-<version> tag). Use when asked to publish, release, upload or resubmit the Windows app, or after a certification failure.
---

# Release the Windows app (Microsoft Store)

Names come from `companion.json` (`windows.dir`, `windows.app`, `windows.release`, `store.microsoft`); the Store ID's
history, the identity, the audience, past submissions and rejections, and anything this app does differently are in the
app's `.agents/notes/release-windows.md`. Read it first.

Store builds are **Release + Native AOT**: they behave differently from the Debug app run day to day (mybackhurts' 1.0.1
to 1.0.3 all crashed at launch while every Debug run was fine). Never upload a package that hasn't passed step 4.

## Go-ahead

Running this skill is the owner's go-ahead for the whole release: build and upload, apply the store push
(`push-microsoft.py prepare --apply`, `store-release.ps1 -Store draft`), write the notes for certification, push the
release commits and tag, and run the release workflow without stopping to ask. Read each dry run and stop only when it
shows something unexpected (images being replaced, a pending submission, a visibility or publish mode that changed).
Still the owner's: new Store screenshots (ask first), credentials (never handle them), and sending the submission to
certification (`push-microsoft.py commit`, `-Store submit`, **Submit**). When another session's unpushed commits sit
on top, push only the release's own: `git push origin <sha>:refs/heads/master`.

## 1. Version

`<windows.dir>/<windows.app>/Package.appxmanifest`, `Identity Version="1.0.N.0"`: higher than anything uploaded before,
including packages that never shipped (the fourth number stays 0 for the Store). The scripts refuse a version whose
`win-<version>` tag exists.

## 2. Preflight

```sh
scripts/verify.sh --all       # includes check-winui.py, the Windows tests, the pins and companion.py conform
```

Every user-visible change is also on the Mac (or says why not), and the README, landing and store-listing claims match
what ships. Purchases can only be tried for real with the Store's own build on a signed-in PC (there is no sandbox): an
add-on the build sells must be submitted in Partner Center first.

## 3 to 5. Build, smoke-test, bundle: one command

On a Windows PC with the .NET 10 SDK and the Visual Studio Build Tools C++ workload (x64 and ARM64, for the AOT link),
in Windows PowerShell or PowerShell 7, from the app's root:

```powershell
laching/tools/windows/store-release.ps1 [-SideBySide] [-Screenshots C:\shots -Languages en,de]
```

Per platform it publishes the unsigned Native AOT package (the Store signs), checks it is native (its executable, no
`runtimeconfig.json`), installs it fresh like a customer's and checks it starts; then it bundles both into
`<bundle>_<version>_x64_arm64.msixbundle` under `build/store-release/<version>` and opens the folder. It puts the Visual
Studio Installer folder on `PATH` when `vswhere` isn't there (an ARM64 PC needs it), and refuses without Developer Mode
(`-NoSmoke` skips the install check: only for a quick look, never for the packages you upload).

- On an ARM64 PC both packages run (x64 emulated, with 60 s to open its window); on an x64 PC the ARM64 one is checked
  native only, and the script says to start it on an ARM64 PC.
- A PC with the Store copy installed: add `-SideBySide` (the test install gets its own identity, the Store copy and its
  data stay). Without it the script refuses rather than delete that copy's data.
- `-Screenshots <folder>` (outside AppData) also opens every window in both themes under Native AOT, from a package with
  screenshot mode compiled in, in `-Languages`: look at a few shots in both themes when any window changed.

**Or let CI build them:** the app's *Windows release* workflow (Actions > Run workflow; it calls Laching's reusable
`windows-release.yml`) publishes both platforms from the pushed commit, smoke-tests them, uploads the bundle as the run's
artifact and tags `win-<version>`; with `store: draft` or `submit` it also pushes the bundle and the listing text. The
version bump must be pushed first.

## 6. Listing

Skip the Store screenshots by default: every language takes long, and bug fixes rarely show in them. Render them only
when a window `store.microsoft.shots` shows changed visibly enough that the listing would look wrong (ask the user when
unsure):

```sh
python3 laching/tools/store-screenshots.py --canvas microsoft             # the source language, from windows.screenshots
python3 laching/tools/store-screenshots.py --canvas microsoft --lang all  # every language: ask first
```

Show the source language's images to the owner and render the rest once they OK them. `python3 laching/tools/store/check.py` checks
the `ms.*` fields against the Store's limits.

## 7. Partner Center

**Through the API** (`laching/tools/store/push-microsoft.py`; the credentials are the user's to set up, never yours:
`laching/tools/store/microsoft-api-setup.md`). Write the notes for certification to a scratch file (what changed, how to
reach the app: the notification area icon, no sign-in needed), then:

```sh
python3 laching/tools/store/push-microsoft.py prepare --bundle <.msixbundle> --notes <notes.txt> [--only package,text,screenshots,art]
python3 laching/tools/store/push-microsoft.py prepare ... --apply   # creates the submission, uploads, saves; doesn't send it
python3 laching/tools/store/push-microsoft.py commit                # sends it to certification: the owner's call
python3 laching/tools/store/push-microsoft.py status                # progress, errors, certification reports
```

Read the dry run before `--apply`: the package and text changes, the image swaps, visibility and publish mode (copied
from the last published submission). Add `screenshots` when step 6 ran, `art` when `store.microsoft.art` changed. Never
edit an API-made submission in Partner Center: the API can't touch it after that (`prepare --replace-pending` deletes it
and starts over). `store-release.ps1 -Store draft|submit` runs the package and text push right after the bundle.

**By hand** (no credentials): `python3 laching/tools/store/import.py` builds the import folder; the user drags the
`.msixbundle` into **Packages** (removing the previous one) and picks the folder in **Store listings > Import listings >
Import folder** (the built-in browser can't pick files). You can fill **Additional Testing Information** (the notes for
certification). Check every section says complete, then **Submit** (ask first unless told to go ahead).

## 8. Wrap up

Commit the version bump (and the source language's Store screenshots, if step 6 rendered them). Tag the commit once the
upload went through: `store-release.ps1` asks after its own push, `store-release.ps1 -TagOnly` tags after an upload by
hand, and the release workflow tags by itself; answer yes to `store-release.ps1`'s tag prompt. Push the release's
commits and the tag (only the release's own, see *Go-ahead*). Record the version, what was uploaded
and the certification status where the app's notes say.
