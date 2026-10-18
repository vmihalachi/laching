#!/usr/bin/env python3
"""The iOS Simulator for the screenshot tools (screens.py's ios capture, check-layout-ios.py), the iOS twin of avd.py:
builds the Debug app for the Simulator, finds or makes a simulator, boots it with accessibility on (the text checks
read fonts from the accessibility tree, which apps build only with it), installs the app and launches it in screenshot
mode, then moves the folder the run drew into (its `<shot>-<theme>.png` and `problems.txt`) out of the app's
container. Not a command of its own.

A launch is `xcrun simctl launch --console-pty` with `-AppleLanguages (<language>)` and `-AppleLocale <region>`
(`region`: en_US, it_IT, pt_BR…), so no reboot between languages; the app prints the folder it drew into last. The
environment reaches the app as `SIMCTL_CHILD_<NAME>`. Needs Xcode (`xcodebuild`, `xcrun simctl`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

LAUNCH_TIMEOUT = 600  # seconds for one run; a simulator under load can hang a launch for good
# The language to give the simulator's region; others get <lang>_<LANG> or the region they name.
REGIONS = {"en": "en_US", "cs": "cs_CZ", "da": "da_DK", "el": "el_GR", "he": "he_IL", "hi": "hi_IN", "ja": "ja_JP",
           "ko": "ko_KR", "nb": "nb_NO", "sv": "sv_SE", "uk": "uk_UA", "vi": "vi_VN", "zh-Hans": "zh_CN",
           "zh-Hant": "zh_TW"}


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
    result = subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True, timeout=timeout)
    if log:
        _append(log, f"$ xcrun simctl {' '.join(args)}", result.stdout, result.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"simctl {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


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
               str(derived), "build", "-quiet"]
    result = subprocess.run(command, cwd=config.mac_dir, capture_output=True, text=True)
    if log:
        _append(log, f"$ {' '.join(command)}", result.stdout, result.stderr)
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

    def accessibility_on(self) -> None:
        """Accessibility on, so the app builds its accessibility tree (the next boot sees it)."""
        for key in ("ApplicationAccessibilityEnabled", "AccessibilityEnabled"):
            self.write_default("com.apple.Accessibility", key, True)

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
        try:
            done = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **child}, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:  # a simulator too busy to launch may be too busy to answer this
                self.simctl("terminate", bundle, timeout=60, check=False)
            except subprocess.TimeoutExpired:
                pass
            raise RuntimeError(f"the run didn't finish in {timeout:.0f} s") from None
        lines = [line.strip() for line in done.stdout.replace("\r", "").splitlines()]
        if self.log:
            _append(self.log, "\n".join(lines), done.stderr)
        # The app draws into its container's temp folder and prints that path last.
        folders = [Path(line) for line in lines if Path(line).is_absolute() and Path(line).is_dir()]
        if not folders:
            tail = "\n".join(lines[-5:])
            raise RuntimeError(f"no shots were drawn (exit {done.returncode}){': ' + tail if tail else ''}")
        shutil.rmtree(out, ignore_errors=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folders[-1]), out)
        return out
