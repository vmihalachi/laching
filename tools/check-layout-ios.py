#!/usr/bin/env python3
"""Renders the iPhone and iPad app's screens in each language on several simulators and fails when one doesn't fit:
content taller than the screen outside a scroll view, text cut short or pushed off the screen, a screen that couldn't
be drawn (ScreenshotCapture's checks, `LayoutCheck+iOS.swift`). The iOS twin of check-layout.sh.

    python3 laching/tools/check-layout-ios.py [--devices se,promax,...] [--only name,name] [--jobs N]
        [--text-size SIZE] [--app PATH] [--out DIR] [--keep] [language ...]

Defaults: every device below, every language in languages.all, LAYOUT_JOBS (else 4; at least one per device type)
simulators at a time. Shots and problems go to <mac.dir>/build/layout-check/ios-<device>/<language>. It builds the Debug
app for the Simulator once (`mac.iosScheme`; --app runs one already built), then clones a simulator per job from a
template it keeps ("<name> layout: <device type>", made once with accessibility on, which the text checks read), boots
the clones, and runs the languages on them side by side, each one launch of the app in screenshot mode
(`mac.screenshotEnv`) with -AppleLanguages and -AppleLocale (the language's region, sim.py): no reboot between
languages. The clones are deleted at the end (--keep leaves them).

Devices: se (iPhone SE 3rd generation, the smallest), promax (the largest iPhone), ipad (iPad Pro 13" upright),
ipad-landscape (the same turned, drawn at the turned screen's size: iPadOS won't turn a windowed app), ipad-split (a
375 pt third of the screen, compact as in Split View), duo (iPhone Duo, closed: nothing opens it from the command
line). --text-size sets Dynamic Type on every clone (`xcrun simctl ui <device> content_size`), such as
accessibility-extra-extra-extra-large, to see what the largest text breaks. A SIMCTL_CHILD_<NAME> variable in the
environment reaches the app as <NAME>. Leftover clones of a run that was killed are deleted by the next run; the
templates stay (`xcrun simctl delete <udid>` removes one).
"""

import argparse
import os
import plistlib
import queue
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion  # noqa: E402
import sim  # noqa: E402
from layoutcheck import log, report  # noqa: E402

TYPE_PREFIX = "com.apple.CoreSimulator.SimDeviceType."
# Device: the simulator types to use, the first one a runtime supports, and how the app shows its window.
DEVICES = {
    "se": {"types": ["iPhone-SE-3rd-generation"]},
    "promax": {"types": ["iPhone-18-Pro-Max", "iPhone-17-Pro-Max", "iPhone-16-Pro-Max"]},
    "ipad": {"types": ["iPad-Pro-13-inch-M5-12GB", "iPad-Pro-13-inch-M4-8GB"]},
    "ipad-landscape": {"types": ["iPad-Pro-13-inch-M5-12GB", "iPad-Pro-13-inch-M4-8GB"], "orientation": "landscape"},
    "ipad-split": {"types": ["iPad-Pro-13-inch-M5-12GB", "iPad-Pro-13-inch-M4-8GB"], "width": 375},
    "duo": {"types": ["iPhone-Duo"]},
}


def pick_type(device: str, runtimes: list[dict]) -> tuple[str, dict]:
    """The first of `device`'s simulator types an installed runtime supports, and the newest such runtime."""
    for short in DEVICES[device]["types"]:
        if runtime := sim.newest_runtime(TYPE_PREFIX + short, runtimes):
            return short, runtime
    sys.exit(f"check-layout-ios: no installed iOS runtime has a simulator for {device} ({', '.join(DEVICES[device]['types'])})")


def template(app_name: str, short: str, runtime: dict) -> str:
    """The template simulator for a type: made once (booted, accessibility on, shut down), kept for later runs."""
    name = f"{app_name} layout: {short}"
    for device in sim.devices():
        if device["name"] == name and device["runtime"] == runtime["identifier"] and device["isAvailable"]:
            if device["state"] != "Shutdown":
                sim.Simulator(device["udid"]).shutdown()
            return device["udid"]
    log(f"Making the {name} simulator ({runtime['name']}), once")
    made = sim.Simulator.create(name, TYPE_PREFIX + short, runtime["identifier"])
    made.boot()
    made.accessibility_on()
    made.simctl("shutdown")
    return made.udid


def clean_stale_clones(app_name: str) -> None:
    """Deletes the clones of runs that are no longer running (killed before they could)."""
    pattern = re.compile(rf"^{re.escape(app_name)} layout clone .* \((\d+)\)$")
    for device in sim.devices():
        match = pattern.match(device["name"])
        if not match:
            continue
        try:
            os.kill(int(match.group(1)), 0)
            continue  # that run is still going
        except ProcessLookupError:
            pass
        except PermissionError:
            continue
        sim.Simulator(device["udid"]).delete()


def run_language(simulator: sim.Simulator, bundle: str, device: str, language: str, only: str, env_name: str,
                 out: Path) -> list[str]:
    """One launch in screenshot mode, its shots and problems.txt in `out`; the problems."""
    env = {env_name: only or "all"}
    if orientation := DEVICES[device].get("orientation"):
        env["LACHING_SCREENSHOT_ORIENTATION"] = orientation
    if width := DEVICES[device].get("width"):
        env["LACHING_SCREENSHOT_WIDTH"] = str(width)
    try:
        simulator.run(bundle, language, env, out)
    except RuntimeError as error:
        return [str(error)]
    problems_file = out / "problems.txt"
    problems = problems_file.read_text(encoding="utf-8").splitlines() if problems_file.exists() else []
    if not any(out.glob("*.png")):
        problems.append(f"no shots matched --only {only}" if only else "no shots were written")
    return [p for p in problems if p.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("languages", nargs="*", help="default: languages.all")
    parser.add_argument("--devices", default=",".join(DEVICES), help=f"comma-separated, of: {', '.join(DEVICES)}")
    parser.add_argument("--only", default="", help="just the shots whose names start with one of these (comma-separated)")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("LAYOUT_JOBS", "4")), help="simulators at a time")
    parser.add_argument("--text-size", help="Dynamic Type on every simulator, as `simctl ui content_size` names it")
    parser.add_argument("--app", type=Path, help="a Debug Simulator build of the app to run instead of building one")
    parser.add_argument("--out", type=Path, help="default: <mac.dir>/build/layout-check")
    parser.add_argument("--keep", action="store_true", help="leave the clones, booted")
    args = parser.parse_args()

    config = companion.load()
    app_name = config.require("name")
    env_name = config.require("mac.screenshotEnv")
    languages = args.languages or config.languages
    devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    if unknown := [d for d in devices if d not in DEVICES]:
        sys.exit(f"check-layout-ios: unknown device {', '.join(unknown)} (one of {', '.join(DEVICES)})")
    out_root = args.out or config.work_dir / "layout-check"
    started = time.monotonic()

    if args.app:
        app = args.app.resolve()
    else:
        log("Building the iOS app (Debug, Simulator)")
        try:
            app = sim.build(config, config.mac_dir / "build" / "ios-layout.noindex")
        except RuntimeError as error:
            sys.exit(f"check-layout-ios: {error}")
    bundle = plistlib.loads((app / "Info.plist").read_bytes())["CFBundleIdentifier"]

    runtimes = sim.runtimes()
    clean_stale_clones(app_name)
    # Devices on the same simulator type share its clones: the iPad's three ways are launches of one app.
    types: dict[str, dict] = {}
    for device in devices:
        short, runtime = pick_type(device, runtimes)
        entry = types.setdefault(short, {"runtime": runtime, "tasks": queue.Queue(), "count": 0})
        for language in languages:
            entry["tasks"].put((device, language))
            entry["count"] += 1
    # Clones per type in proportion to its runs, at least one each.
    total = sum(t["count"] for t in types.values())
    jobs = max(args.jobs, len(types))
    for t in types.values():
        t["workers"] = max(1, round(jobs * t["count"] / total))
    while sum(t["workers"] for t in types.values()) > jobs:
        busiest = max((t for t in types.values() if t["workers"] > 1), key=lambda t: t["workers"] / t["count"])
        busiest["workers"] -= 1
    for short, t in types.items():
        t["workers"] = min(t["workers"], t["count"])
        t["template"] = template(app_name, short, t["runtime"])

    results: dict[tuple[str, str], list[str]] = {}
    clones: list[sim.Simulator] = []
    clones_lock = threading.Lock()

    def worker(short: str, number: int) -> None:
        entry = types[short]
        try:
            name = f"{app_name} layout clone {short} #{number} ({os.getpid()})"
            simulator = sim.Simulator(sim.simctl("clone", entry["template"], name).strip())
            with clones_lock:
                clones.append(simulator)
            simulator.boot()
            simulator.accessibility_on()
            if args.text_size:
                simulator.simctl("ui", "content_size", args.text_size)
            simulator.install(app)
        except Exception as error:  # noqa: BLE001 - the runs it would have made report it
            log(f"check-layout-ios: the {short} simulator #{number} didn't start ({error})")
            while True:
                try:
                    device, language = entry["tasks"].get_nowait()
                except queue.Empty:
                    return
                # Another clone of the type may still take it; this one can't.
                if entry["workers"] > 1:
                    entry["tasks"].put((device, language))
                    entry["workers"] -= 1
                    return
                results[(device, language)] = [f"the simulator didn't start ({error})"]
        while True:
            try:
                device, language = entry["tasks"].get_nowait()
            except queue.Empty:
                return
            began = time.monotonic()
            try:
                problems = run_language(simulator, bundle, device, language, args.only, env_name,
                                        out_root / f"ios-{device}" / language)
            except Exception as error:  # noqa: BLE001
                problems = [f"the run failed ({error})"]
            results[(device, language)] = problems
            log(f"  ios-{device} {language}: {len(problems)} problem(s), {time.monotonic() - began:.0f} s")

    threads = [threading.Thread(target=worker, args=(short, n + 1), daemon=True)
               for short, t in types.items() for n in range(t["workers"])]
    log(f"Running {total} launch(es) on {len(threads)} simulator(s): {', '.join(devices)} × {' '.join(languages)}")
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        if not args.keep:
            for simulator in clones:
                simulator.delete()
    return report(results, "ios", devices, languages, out_root, started)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
