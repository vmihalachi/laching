#!/usr/bin/env python3
"""Renders the Android app's screens in each language on headless emulators and fails when one doesn't fit: content
taller than the screen outside a scroll view, text cut short or pushed off the screen, a screen that couldn't be drawn
(ScreenshotRun's checks, `laching.android`). The Android twin of check-layout-ios.py.

    python3 laching/tools/check-layout-android.py [--devices AVD,AVD] [--only shot,shot] [--jobs N]
        [--font-scale 2.0] [--mode MODE] [--apk PATH] [--cold] [--out DIR] [language ...]

Defaults: the AVDs of the `screens.platforms` entries captured on android (their `avd`), every language in
languages.all, LAYOUT_JOBS (else 2) emulators at a time. Shots and problems go to <work dir>/layout-check/
android-<avd>/<language> (problems.txt as the app wrote it; the emulator's log beside the languages). It builds the
Debug APK once (`./gradlew :<android.app>:assembleDebug` in `android.dir`; --apk runs one already built), boots each
AVD without a window (avd.py: from the snapshot its first run saved; --cold boots it cold and saves a new one), installs the APK and runs the languages on it, each one launch per distinct shot env in
screenshot mode with Laching's intent extras (`LACHING_SCREENSHOT_MODE`, `LACHING_SCREENS`, `LACHING_LANG`,
`LACHING_REGION`): no reboot between languages. The shots are the ones `screens.list` names for the AVD's platform,
each with its `env` (--only names other shots, in one launch); --mode is the screenshot mode of shots whose env sets
none ("screens"). --font-scale sets the system font scale for the run (`settings put system font_scale`; 2.0 is the
largest a user can pick) to see what the largest text breaks. The emulators are killed at the end.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import avd  # noqa: E402
import companion  # noqa: E402
import screens  # noqa: E402
import sim  # noqa: E402
from layoutcheck import log, report  # noqa: E402


def build(config: companion.Config) -> Path:
    """The Debug APK, built now."""
    command, cwd = avd.gradle(config)
    log(f"Building the Debug APK ({' '.join(command[1:])} in {cwd})")
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"check-layout-android: the build failed:\n{(done.stdout + done.stderr).strip()}")
    return avd.apk(config)


def android_platforms(config: companion.Config) -> list[screens.Platform]:
    section = config.get("screens") or {}
    return [screens.Platform(name, value["capture"], value) for name, value in section.get("platforms", {}).items()
            if value["capture"] == "android"]


def launches(config: companion.Config, device: str, only: list[str], mode: str) -> list[tuple[dict, list[str], bool]]:
    """(extras, shots, camera) per launch of one language: --only's shots in one, else the AVD's platform's shots from
    `screens.list` grouped by their env."""
    if only:
        return [({avd.MODE: mode}, only, False)]
    platforms = android_platforms(config)
    platform = next((p for p in platforms if p.settings.get("avd") == device), platforms[0] if platforms else None)
    if platform is None:
        sys.exit("check-layout-android: no android platform in screens.platforms; name the shots with --only")
    manifest = screens.Screens(config)
    grouped: dict[tuple, list[screens.Shot]] = {}
    for screen in manifest.list:
        if shot := manifest.shot(screen, platform):
            grouped.setdefault(shot.env, []).append(shot)
    if not grouped:
        sys.exit(f"check-layout-android: screens.list names no shot for {platform.name}; name them with --only")
    return [({avd.MODE: mode, **dict(env)}, list(dict.fromkeys(shot.name for shot in shots)),
             any(shot.camera for shot in shots)) for env, shots in grouped.items()]


def problems_in(out: Path, shots: list[str]) -> list[str]:
    """The run's problems.txt lines, plus the shots it didn't draw."""
    listed = out / "problems.txt"
    problems = [line.strip() for line in listed.read_text("utf8").splitlines() if line.strip()] if listed.is_file() else []
    for shot in shots:
        for theme in ("light", "dark"):
            if not (out / f"{shot}-{theme}.png").is_file():
                problems.append(f"{shot}-{theme}.png: not drawn")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("languages", nargs="*", help="default: languages.all")
    parser.add_argument("--devices", default="", help="AVD names, comma-separated; default: screens.platforms' android avds")
    parser.add_argument("--only", default="", help="just these shots (comma-separated), in one launch")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("LAYOUT_JOBS", "2")), help="emulators at a time")
    parser.add_argument("--font-scale", type=float, help="the system font scale for the run, e.g. 2.0")
    parser.add_argument("--mode", default=avd.DEFAULT_MODE, help="LACHING_SCREENSHOT_MODE for shots whose env sets none")
    parser.add_argument("--apk", type=Path, help="a Debug APK to run instead of building one")
    parser.add_argument("--cold", action="store_true", help="cold-boot each AVD and save its snapshot anew")
    parser.add_argument("--out", type=Path, help="default: <work dir>/layout-check")
    args = parser.parse_args()

    config = companion.load()
    languages = args.languages or config.languages
    devices = [d.strip() for d in args.devices.split(",") if d.strip()] or [
        p.settings["avd"] for p in android_platforms(config) if p.settings.get("avd")]
    if not devices:
        sys.exit("check-layout-android: no AVD: --devices NAME, or an android platform with an avd in screens.platforms")
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    out_root = args.out or config.work_dir / "layout-check"
    started = time.monotonic()
    apk = args.apk.resolve() if args.apk else build(config)
    app_id = avd.app_id(config)
    plan = {device: launches(config, device, only, args.mode) for device in devices}

    results: dict[tuple[str, str], list[str]] = {}
    slots = threading.Semaphore(max(1, args.jobs))

    def worker(index: int, device: str) -> None:
        with slots:
            folder = out_root / f"android-{device}"
            folder.mkdir(parents=True, exist_ok=True)
            emulator_log = folder / "emulator.log"
            emulator_log.write_text("", "utf8")
            try:
                emulator = avd.Emulator(device, index, emulator_log, camera=any(camera for _, _, camera in plan[device]),
                                        cold=args.cold)
                emulator.boot()
                emulator.install(apk)
                component = emulator.launcher(app_id)
                if args.font_scale:
                    emulator.font_scale(args.font_scale)
            except (RuntimeError, OSError) as error:
                log(f"check-layout-android: {device} didn't start ({error})")
                for language in languages:
                    results[(device, language)] = [f"the emulator didn't start ({error})"]
                if "emulator" in locals():
                    emulator.kill()
                return
            try:
                for language in languages:
                    began = time.monotonic()
                    out = folder / language
                    problems: list[str] = []
                    for number, (extras, shots, _) in enumerate(plan[device]):
                        try:
                            emulator.run(app_id, component, language, sim.region(language).partition("_")[2], shots,
                                         extras, out, clear=number == 0)
                        except (RuntimeError, OSError) as error:
                            problems.append(f"the run failed ({error})")
                    problems += problems_in(out, [shot for _, shots, _ in plan[device] for shot in shots])
                    results[(device, language)] = problems
                    log(f"  android-{device} {language}: {len(problems)} problem(s), {time.monotonic() - began:.0f} s")
            finally:
                if args.font_scale:
                    emulator.font_scale(None)
                emulator.kill()

    threads = [threading.Thread(target=worker, args=(index, device), daemon=True) for index, device in enumerate(devices)]
    runs = sum(len(plan[device]) for device in devices) * len(languages)
    log(f"Running {runs} launch(es) on {len(devices)} emulator(s): {', '.join(devices)} × {' '.join(languages)}")
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return report(results, "android", devices, languages, out_root, started)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
