#!/usr/bin/env python3
"""A headless Android emulator for the screenshot tools (screens.py's android capture, check-layout-android.py): boots
an AVD without a window, puts the status bar in demo mode (09:41, full battery, no notifications), installs the Debug
APK and launches the app in screenshot mode with Laching's intent extras, then waits for the run's `done` file and
copies `files/screens/` off the device. Not a command of its own.

The app's side is `laching.android.LaunchContext` and `ScreenshotRun`: `LACHING_SCREENSHOT_MODE` marks a screenshot
launch (the app's own mode names; "screens" when the shot's env names none), `LACHING_SCREENS` lists the shots,
`LACHING_LANG` and `LACHING_REGION` give the locale, so no reboot between languages; the app writes
`files/screens/<shot>-<theme>.png`, `problems.txt` and `done`, then finishes. The files come back through
`adb exec-out run-as <app> cat`, which works on every debuggable build (adb pull can't read an app's private files).
Each command goes to the log with the seconds it took, as do the boot and each run.

Needs the Android SDK: `emulator` and `adb` on PATH or under $ANDROID_HOME ($ANDROID_SDK_ROOT, else
~/Library/Android/sdk, ~/Android/Sdk, or Android/Sdk under %LOCALAPPDATA% on Windows). The Debug APK comes from
`./gradlew :<android.app>:assembleDebug` in `android.dir`; its application id is `android.devApplicationId`
(`devBundleId`), else the release one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

MODE = "LACHING_SCREENSHOT_MODE"
SCREENS = "LACHING_SCREENS"
LANG = "LACHING_LANG"
REGION = "LACHING_REGION"
DEFAULT_MODE = "screens"
BOOT_TIMEOUT = 300  # seconds for the AVD to boot
LAUNCH_TIMEOUT = 600  # seconds for one run of the app
FIRST_PORT = 5554  # adb's emulator ports: 5554, 5556, … (each AVD takes a console and an adb port)


def sdk_tool(name: str) -> str:
    """`emulator` or `adb`: on PATH, else under the SDK (`adb.exe` and Android Studio's folder on Windows)."""
    if found := shutil.which(name):
        return found
    local = os.environ.get("LOCALAPPDATA")
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             Path.home() / "Library" / "Android" / "sdk", Path.home() / "Android" / "Sdk",
             Path(local) / "Android" / "Sdk" if local else None]
    folder = {"emulator": "emulator", "adb": "platform-tools"}[name]
    file = f"{name}.exe" if os.name == "nt" else name
    for root in roots:
        if root and (path := Path(root) / folder / file).is_file():
            return str(path)
    raise RuntimeError(f"no {name} on PATH or under ANDROID_HOME (the Android SDK's {folder})")


def avds() -> list[str]:
    """The AVDs on this machine (`emulator -list-avds`)."""
    out = subprocess.run([sdk_tool("emulator"), "-list-avds"], capture_output=True, text=True, timeout=60).stdout
    return [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("INFO")]


def gradle(config) -> tuple[list[str], Path]:
    """The command that builds the Debug APK, and the folder to run it in."""
    folder = config.require_path("android.dir")
    wrapper = folder / ("gradlew.bat" if os.name == "nt" else "gradlew")
    return [str(wrapper), f":{config.get('android.app', 'app')}:assembleDebug", "-q", "--console=plain"], folder


def apk(config) -> Path:
    """The newest Debug APK the build left."""
    app = config.get("android.app", "app")
    folder = config.require_path("android.dir") / app / "build" / "outputs" / "apk" / "debug"
    found = sorted(folder.glob("*.apk"), key=lambda path: path.stat().st_mtime)
    if not found:
        raise RuntimeError(f"no Debug APK in {folder} (./gradlew :{app}:assembleDebug)")
    return found[-1]


def app_id(config) -> str:
    """The Debug build's application id: the dev one when the app has it."""
    found = (config.get("android.devApplicationId") or config.get("devBundleId") or config.get("android.applicationId")
             or config.get("bundleId"))
    if not found:
        raise RuntimeError("no android.applicationId (or bundleId) in companion.json")
    return found


class Emulator:
    """One headless AVD, from `boot()` to `kill()`; everything it runs goes to `log`."""

    def __init__(self, avd: str, index: int, log: Path, camera: bool = False) -> None:
        self.avd = avd
        self.port = FIRST_PORT + 2 * index
        self.serial = f"emulator-{self.port}"
        self.log = log
        self.camera = camera  # the host's webcam as the front camera (-camera-front webcam0)
        self.process: subprocess.Popen | None = None
        self.adb_path = sdk_tool("adb")

    def note(self, text: str) -> None:
        with self.log.open("a", encoding="utf8") as out:
            out.write(text.rstrip("\n") + "\n")

    def adb(self, *args: str, timeout: float = 120, binary: bool = False,
            quiet: bool = False) -> subprocess.CompletedProcess:
        command = [self.adb_path, "-s", self.serial, *args]
        if not quiet:
            self.note(f"$ adb -s {self.serial} {' '.join(args)}")
        began = time.monotonic()
        done = subprocess.run(command, capture_output=True, text=not binary, timeout=timeout)
        if not binary and not quiet and (text := (done.stdout + done.stderr).strip()):
            self.note(text)
        if not quiet:
            self.note(f"({time.monotonic() - began:.1f} s)")
        return done

    def shell(self, *args: str, timeout: float = 120, quiet: bool = False) -> str:
        return self.adb("shell", *args, timeout=timeout, quiet=quiet).stdout.strip()

    def boot(self) -> None:
        """Starts the emulator and waits for Android to come up, then enters demo mode."""
        command = [sdk_tool("emulator"), "-avd", self.avd, "-port", str(self.port), "-no-window", "-no-audio",
                   "-no-boot-anim", "-no-snapshot", "-gpu", "swiftshader_indirect"]
        if self.camera:
            command += ["-camera-front", "webcam0"]
        self.note("$ " + " ".join(command))
        with self.log.open("a", encoding="utf8") as out:
            self.process = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        began = time.monotonic()
        deadline = began + BOOT_TIMEOUT
        self.adb("wait-for-device", timeout=BOOT_TIMEOUT)
        while self.shell("getprop", "sys.boot_completed", quiet=True) != "1":
            if self.process.poll() is not None:
                raise RuntimeError(f"the emulator exited ({self.process.returncode}); see {self.log}")
            if time.monotonic() > deadline:
                self.kill()
                raise RuntimeError(f"{self.avd} didn't boot in {BOOT_TIMEOUT} s; see {self.log}")
            time.sleep(2)
        self.note(f"(booted in {time.monotonic() - began:.1f} s)")
        self.demo_mode()

    def demo_mode(self) -> None:
        """The status bar as the stores show it: 09:41, a full battery, Wi-Fi, no notifications."""
        self.shell("settings", "put", "global", "sysui_demo_allowed", "1")
        for extras in (("command", "enter"), ("command", "clock", "hhmm", "0941"),
                       ("command", "battery", "level", "100", "plugged", "false"),
                       ("command", "network", "wifi", "show", "level", "4"), ("command", "network", "mobile", "hide"),
                       ("command", "notifications", "visible", "false")):
            pairs = [arg for key, value in zip(extras[::2], extras[1::2]) for arg in ("-e", key, value)]
            self.shell("am", "broadcast", "-a", "com.android.systemui.demo", *pairs)

    def font_scale(self, scale: float | None) -> None:
        """The system font scale (1.0 is the default; 2.0 the largest a user can pick)."""
        self.shell("settings", "put", "system", "font_scale", str(scale or 1.0))

    def install(self, apk: Path) -> None:
        """Installs (or replaces) the APK with its runtime permissions granted, so a camera shot needs no dialog."""
        done = self.adb("install", "-r", "-g", str(apk), timeout=600)
        if done.returncode != 0:
            raise RuntimeError(f"couldn't install {apk.name}: {(done.stderr or done.stdout).strip()}")

    def launcher(self, app_id: str) -> str:
        """The app's launcher activity as `am start -n` wants it."""
        out = self.shell("cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LAUNCHER",
                         app_id)
        found = [line.strip() for line in out.splitlines() if "/" in line]
        if not found:
            raise RuntimeError(f"no launcher activity in {app_id} ({out.strip() or 'not installed'})")
        return found[-1]

    def run(self, app_id: str, component: str, language: str, region: str, shots: list[str], extras: dict[str, str],
            raw: Path, timeout: float = LAUNCH_TIMEOUT, clear: bool = True) -> None:
        """One launch in screenshot mode: the shots and problems.txt land in `raw` (emptied first, unless `clear` is
        False: then the new problems.txt lines follow the old ones). Raises when the run doesn't finish in time."""
        self.shell("am", "force-stop", app_id)
        self.shell("run-as", app_id, "rm", "-rf", "files/screens")
        command = ["am", "start", "-W", "-n", component, "--es", MODE, extras.get(MODE, DEFAULT_MODE),
                   "--es", SCREENS, ",".join(shots), "--es", LANG, language, "--es", REGION, region]
        for name, value in extras.items():
            if name != MODE:
                command += ["--es", name, value]
        self.shell(*command)
        began = time.monotonic()
        deadline = began + timeout
        while self.adb("shell", "run-as", app_id, "ls", "files/screens/done", quiet=True).returncode != 0:
            if time.monotonic() > deadline:
                self.shell("am", "force-stop", app_id)
                raise RuntimeError(f"the run didn't finish in {timeout:.0f} s")
            time.sleep(2)
        self.note(f"(drew in {time.monotonic() - began:.1f} s)")
        if clear:
            shutil.rmtree(raw, ignore_errors=True)
        raw.mkdir(parents=True, exist_ok=True)
        for name in self.shell("run-as", app_id, "ls", "files/screens").split():
            if name == "done":
                continue
            data = self.adb("exec-out", "run-as", app_id, "cat", f"files/screens/{name}", binary=True, quiet=True).stdout
            if name == "problems.txt" and not clear and (raw / name).is_file():
                data = (raw / name).read_bytes() + data
            (raw / name).write_bytes(data)

    def kill(self) -> None:
        if self.process is None:
            return
        self.adb("emu", "kill", timeout=30)
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None
