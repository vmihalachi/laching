#!/usr/bin/env python3
"""The iOS Simulator for the screenshot tools (screens.py's ios capture, check-layout-ios.py), the iOS twin of avd.py:
builds the Debug app for the Simulator, finds or makes a simulator, boots it with accessibility on (the text checks
read fonts from the accessibility tree, which apps build only with it), installs the app and launches it in screenshot
mode, then moves the folder the run drew into (its `<shot>-<theme>.png` and `problems.txt`) out of the app's
container. Not a command of its own.

A launch is `xcrun simctl launch --console-pty` with `-AppleLanguages (<language>)` and `-AppleLocale <region>`
(`region`: en_US, it_IT, pt_BR…), so no reboot between languages; the app prints the folder it drew into last. The
environment reaches the app as `SIMCTL_CHILD_<NAME>`. Languages run side by side on clones of one simulator
(`on_clones`), named `<name> clone #<n> (<pid>)` so the next run deletes the ones a killed run left. Every command goes
to the log with the seconds it took. Needs Xcode (`xcodebuild`, `xcrun simctl`).
"""

from __future__ import annotations

import json
import os
import plistlib
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import companion

# The simulators' folders (simctl's default set, the one these tools use): each one's data, preferences included.
DEVICES = Path.home() / "Library" / "Developer" / "CoreSimulator" / "Devices"
LAUNCH_TIMEOUT = 600  # seconds for one run; a simulator under load can hang a launch for good
# The language to give the simulator's region; others get <lang>_<LANG> or the region they name.
REGIONS = {"en": "en_US", "cs": "cs_CZ", "da": "da_DK", "el": "el_GR", "he": "he_IL", "hi": "hi_IN", "ja": "ja_JP",
           "ko": "ko_KR", "nb": "nb_NO", "sv": "sv_SE", "uk": "uk_UA", "vi": "vi_VN", "zh-Hans": "zh_CN",
           "zh-Hant": "zh_TW"}
# What the fit checks need: accessibility on, so the app builds its accessibility tree. Read by the next boot.
ACCESSIBILITY = (("com.apple.Accessibility", "ApplicationAccessibilityEnabled", True),
                 ("com.apple.Accessibility", "AccessibilityEnabled", True))


def region(language: str) -> str:
    """The locale a language runs in (`-AppleLocale`, and the Android region after the `_`): en_US, pt_BR, zh_TW…"""
    if language in REGIONS:
        return REGIONS[language]
    if "-" in language:
        base, _, area = language.partition("-")
        return f"{base}_{area.upper()}"
    return f"{language}_{language.upper()}"


def simctl(*args: str, timeout: float = 300, check: bool = True, log: Path | None = None) -> str:
    """`xcrun simctl` and its stdout, appended to `log` with the command when given; a RuntimeError when it fails and
    `check`."""
    began = time.monotonic()
    result = subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True, timeout=timeout)
    if log:
        _append(log, f"$ xcrun simctl {' '.join(args)} {took(began)}", result.stdout, result.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"simctl {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def took(began: float) -> str:
    """The seconds since `began` (a `time.monotonic()`) as the logs show them after a command: `(12.3 s)`."""
    return f"({time.monotonic() - began:.1f} s)"


def _append(log: Path, *parts: str) -> None:
    with log.open("a", encoding="utf8") as out:
        out.write("\n".join(part.strip() for part in parts if part.strip()) + "\n")


def runtimes() -> list[dict]:
    """The installed iOS runtimes that can run."""
    return [r for r in json.loads(simctl("list", "runtimes", "-j"))["runtimes"]
            if r.get("isAvailable", True) and r.get("platform", "iOS") == "iOS"]


def devices() -> list[dict]:
    """Every simulator, with its runtime's identifier as `runtime`."""
    return [dict(device, runtime=runtime)
            for runtime, found in json.loads(simctl("list", "devices", "-j"))["devices"].items() for device in found]


def newest_runtime(device_type: str, among: list[dict]) -> dict | None:
    """The newest of `among` that runs `device_type` (a device type's name or identifier), None when none does."""
    supporting = [r for r in among if any(device_type in (t.get("name"), t.get("identifier"))
                                          for t in r.get("supportedDeviceTypes", []))]
    return max(supporting, key=lambda r: [int(n) for n in re.findall(r"\d+", r["version"])], default=None)


def derived_data(config) -> Path:
    """Where the Debug Simulator build goes: one folder for screens.py and check-layout-ios.py, so one tool's build
    leaves the other an incremental one. Each tool builds once, before it boots anything, and never while it runs the
    app, so sharing it is safe; only running both tools at the same moment would make two builds race for it."""
    return config.mac_dir / "build" / "ios-simulator.noindex"


def built_app(config, derived: Path) -> Path | None:
    """The Simulator app a build into `derived` made: the one named `mac.app`, else the first; None before a build."""
    apps = sorted((derived / "Build" / "Products" / "Debug-iphonesimulator").glob("*.app"))
    named = [app for app in apps if app.stem == config.get("mac.app")]
    return (named or apps or [None])[0]


def build(config, derived: Path, log: Path | None = None) -> Path:
    """Builds the Debug app for the Simulator (`mac.iosScheme`) into `derived`, its output appended to `log` when given;
    the app, or a RuntimeError saying why there's none."""
    command = ["xcodebuild", "-project", config.require("mac.project"), "-scheme", config.require("mac.iosScheme"),
               "-configuration", "Debug", "-destination", "generic/platform=iOS Simulator", "-derivedDataPath",
               str(derived), "build", "-quiet", *companion.xcode_cache_settings(mapped=True)]
    began = time.monotonic()
    result = subprocess.run(command, cwd=config.mac_dir, capture_output=True, text=True)
    if log:
        _append(log, f"$ {' '.join(command)} {took(began)}", result.stdout, result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"the iOS build failed; see {log}" if log else
                           f"the iOS build failed\n{result.stdout[-4000:]}{result.stderr[-4000:]}")
    if (app := built_app(config, derived)) is None:
        raise RuntimeError(f"the iOS build made no .app in {derived}")
    return app


class Simulator:
    """One simulator, by UDID. Its simctl commands and the app's output go to `log` when given."""

    def __init__(self, udid: str, log: Path | None = None):
        self.udid = udid
        self.log = log

    @classmethod
    def create(cls, name: str, device_type: str, runtime: str, log: Path | None = None) -> Simulator:
        return cls(simctl("create", name, device_type, runtime, log=log).strip(), log)

    def simctl(self, command: str, *args: str, timeout: float = 300, check: bool = True) -> str:
        """`xcrun simctl <command> <udid> <args…>`."""
        return simctl(command, self.udid, *args, timeout=timeout, check=check, log=self.log)

    def boot(self) -> None:
        """Boots it (already booted is fine) and waits until it has."""
        self.simctl("boot", check=False)
        self.simctl("bootstatus", "-b", timeout=600)

    def write_default(self, domain: str, key: str, value: bool) -> None:
        self.simctl("spawn", "defaults", "write", domain, key, "-bool", "true" if value else "false")

    def defaults_set(self, defaults: tuple[tuple[str, str, bool], ...]) -> bool:
        """Whether `defaults` are all written, read from its preference files on this Mac: no boot needed (a `simctl
        spawn defaults read` costs 5 to 6 s each, more than the reboot it would save). Read it shut down: booted, a
        write may not have reached the file yet."""
        preferences = DEVICES / self.udid / "data" / "Library" / "Preferences"
        written: dict[str, dict] = {}
        for domain, key, value in defaults:
            if domain not in written:
                try:
                    written[domain] = plistlib.loads((preferences / f"{domain}.plist").read_bytes())
                except (OSError, plistlib.InvalidFileException):
                    written[domain] = {}
            if written[domain].get(key) != value:
                return False
        return True

    def boot_with(self, defaults: tuple[tuple[str, str, bool], ...]) -> bool:
        """Boots it with `defaults` ((domain, key, bool) triples) in effect. They take effect on the next boot, so only
        when one isn't written yet (`defaults_set`, read before booting) it boots, writes them, shuts down and boots
        again: a simulator kept between runs (or cloned from one) boots once. True when it had to boot twice."""
        if self.defaults_set(defaults):
            if self.log:
                _append(self.log, "(the defaults are written: one boot)")
            self.boot()
            return False
        self.boot()
        for domain, key, value in defaults:
            self.write_default(domain, key, value)
        self.shutdown()
        self.boot()
        return True

    def install(self, app: Path) -> None:
        self.simctl("install", str(app), timeout=600)

    def shutdown(self) -> None:
        self.simctl("shutdown", check=False)

    def delete(self) -> None:
        self.shutdown()
        simctl("delete", self.udid, check=False, log=self.log)

    def run(self, bundle: str, language: str, env: dict[str, str], out: Path, timeout: float = LAUNCH_TIMEOUT) -> Path:
        """One launch of the app `bundle` in screenshot mode, in `language` and its region, `env` reaching the app: moves
        the folder it drew into to `out` (replacing it) and returns it. A RuntimeError when the run doesn't finish in
        `timeout` seconds or draws nothing."""
        child = {f"SIMCTL_CHILD_{name}": value for name, value in env.items()}
        command = ["xcrun", "simctl", "launch", "--console-pty", "--terminate-running-process", self.udid, bundle,
                   "-AppleLanguages", f"({language})", "-AppleLocale", region(language)]
        if self.log:
            _append(self.log, f"$ {' '.join(f'{k}={v}' for k, v in child.items())} {' '.join(command)}")
        began = time.monotonic()
        try:
            done = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **child}, timeout=timeout)
        except subprocess.TimeoutExpired:
            if self.log:
                _append(self.log, f"(gave up after {timeout:.0f} s)")
            try:  # a simulator too busy to launch may be too busy to answer this
                self.simctl("terminate", bundle, timeout=60, check=False)
            except subprocess.TimeoutExpired:
                pass
            raise RuntimeError(f"the run didn't finish in {timeout:.0f} s") from None
        lines = [line.strip() for line in done.stdout.replace("\r", "").splitlines()]
        if self.log:
            _append(self.log, "\n".join(lines), done.stderr, took(began))
        # The app draws into its container's temp folder and prints that path last.
        folders = [Path(line) for line in lines if Path(line).is_absolute() and Path(line).is_dir()]
        if not folders:
            tail = "\n".join(lines[-5:])
            raise RuntimeError(f"no shots were drawn (exit {done.returncode}){': ' + tail if tail else ''}")
        shutil.rmtree(out, ignore_errors=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folders[-1]), out)
        return out


# Clones: one simulator's languages side by side.

CLONE = re.compile(r" clone .*#\d+ \((\d+)\)$")  # `<name> clone #<n> (<pid>)`; check-layout-ios's older names too


def clone_name(name: str, number: int) -> str:
    """The name of a clone this process makes: `<name> clone #<number> (<pid>)`, so a later run can tell it's stale."""
    return f"{name} clone #{number} ({os.getpid()})"


def clean_stale_clones(app_name: str) -> None:
    """Deletes the clones (of `app_name`'s simulators, by either tool) whose run is no longer going: killed before it
    could delete them."""
    for device in devices():
        match = CLONE.search(device["name"])
        if not match or not device["name"].startswith(f"{app_name} "):
            continue
        try:
            os.kill(int(match.group(1)), 0)
            continue  # that run is still going
        except ProcessLookupError:
            pass
        except PermissionError:
            continue
        Simulator(device["udid"]).delete()


def shares(jobs: int, runs: dict[str, int]) -> dict[str, int]:
    """Simulators per group for `jobs` at a time in all (at least one per group): in proportion to each group's `runs`,
    never more than its runs."""
    total = sum(runs.values())
    jobs = max(jobs, len(runs))
    workers = {group: max(1, round(jobs * count / total)) for group, count in runs.items()}
    while sum(workers.values()) > jobs:
        busiest = max((g for g in workers if workers[g] > 1), key=lambda g: workers[g] / runs[g])
        workers[busiest] -= 1
    return {group: min(count, runs[group]) for group, count in workers.items()}


@dataclass
class Clones:
    """One group of `on_clones`: `workers` clones of the shut-down simulator `template`, named after `name`, working
    through `tasks`. Clone n logs to `<log's stem>-<n>.log` when `log` is given."""

    template: str
    name: str
    tasks: list
    workers: int
    log: Path | None = None

    def clone_log(self, number: int) -> Path | None:
        return self.log.with_name(f"{self.log.stem}-{number}{self.log.suffix}") if self.log else None


def on_clones(groups: list[Clones], prepare: Callable[[Simulator], None], work: Callable[[Simulator, object], None],
              failed: Callable[[object, Exception], None], note: Callable[[str], None] = lambda line: None,
              keep: bool = False) -> None:
    """Runs every group's tasks side by side, each group on its own clones: each clone is made from the group's
    template, handed to `prepare` (boot, defaults, install…) and then takes the group's tasks one at a time,
    `work(clone, task)`, until none are left. A clone that doesn't start says so to `note` and leaves its tasks to the
    group's other clones; when none started, each task goes to `failed(task, error)`. `work` catches its own errors.
    Returns when every task is done, the clones deleted (unless `keep`: left booted)."""
    made: list[Simulator] = []
    lock = threading.Lock()

    def worker(group: Clones, pending: queue.Queue, alive: list[int], number: int) -> None:
        try:
            log = group.clone_log(number)
            clone = Simulator(simctl("clone", group.template, clone_name(group.name, number), log=log).strip(), log)
            with lock:
                made.append(clone)
            prepare(clone)
        except Exception as error:  # noqa: BLE001 - its tasks go to the other clones, or to failed()
            note(f"{clone_name(group.name, number)} didn't start ({error})")
            with lock:
                alive[0] -= 1
                last = alive[0] == 0
            if not last:
                return
            while True:
                try:
                    failed(pending.get_nowait(), error)
                except queue.Empty:
                    return
        while True:
            try:
                task = pending.get_nowait()
            except queue.Empty:
                return
            work(clone, task)

    threads = []
    for group in groups:
        pending: queue.Queue = queue.Queue()
        for task in group.tasks:
            pending.put(task)
        alive = [min(group.workers, len(group.tasks))]
        threads += [threading.Thread(target=worker, args=(group, pending, alive, n + 1), daemon=True)
                    for n in range(alive[0])]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        if not keep:
            for clone in made:
                clone.delete()
