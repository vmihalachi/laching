#!/usr/bin/env python3
"""The same screens on every platform and in every language: captured in one run, shown side by side, and narrowed to
what changed since the last accepted run.

    laching/tools/screens.py capture [--platforms mac,windows,ios,android] [--langs en,fr|all]
                                      [--only screen,…] [--themes light,dark] [--jobs N] [--no-build] [--cold]
    laching/tools/screens.py compare [--langs …] [--only …] [--themes …]     # sheets and index.html
    laching/tools/screens.py diff [--against NAME] [--threshold BITS]       # what changed since NAME was accepted
    laching/tools/screens.py accept [NAME]                                   # the captures as they are become NAME

companion.json's `screens` names the platforms and the screens (schema: companion.schema.json). Each canonical screen
maps to each platform's shot: `{"today": {"mac": "home", "windows": "home", "ios": "today"}}`, by the platform's own
name or its capture kind (mac, windows, ios, android), null where it has none. Everything goes under `screens.out`
(build/screens), which stays out of git:

    <platform>/<lang>/<screen>-<theme>.png    the captures, under the canonical names
    problems.txt                              each platform's problems from its last capture, in one list
    sheets/<lang>/<screen>-<theme>.jpg        one sheet per screen, language and theme: landscape platforms on top,
                                              portrait ones below, each row scaled to one height
    index.html                                the sheets to flip through by screen, language and theme
    baselines/<NAME>.json, changes.json       the accepted run's perceptual hashes, and what differs from them

`capture` builds each platform once and runs them side by side: the Mac through `screens.platforms.<p>.script` (or
Laching's screenshots.sh), hidden, its languages side by side; Windows in the Parallels VM through
`windows/vm.sh check-layout` (or `vm.sh screenshots` once per language with `"vm": "screenshots"`); iOS on simulators
of its own, one per device ("<name> screens <platform>", made once with its defaults written, so it boots once), side
by side, each language one launch of the app in screenshot mode (`mac.screenshotEnv`, as check-layout-ios.py runs
it), which draws its window without the status bar; shots with an `env` of their own get a launch of their own; a
device given more than one simulator runs its languages side by side on clones of its simulator (sim.py's `on_clones`,
deleted at the end); Android on headless emulators (`avd`, one per platform; avd.py), each language one launch with
Laching's intent extras (`LACHING_SCREENSHOT_MODE`, `LACHING_SCREENS`, `LACHING_LANG`, `LACHING_REGION`; a shot's `env`
adds extras, `camera` gives the emulator the host's webcam; the platform's `posture` folds a foldable AVD and its
`orientation` turns the screen), the status bar in demo mode at 09:41, each emulator
quick-booting from the snapshot its first run saved (`--cold` boots them cold and saves new ones). `--jobs`
(LAYOUT_JOBS, 3) is the whole run's budget: the Mac and Windows app processes, iOS simulators and Android emulators at
once in all, shared among the platforms in proportion to their runs (sim.py's `shares`), at least one each, so a
capture of every platform no longer runs `--jobs` of each at once. `--themes` reaches every launch
(`LACHING_SCREENSHOT_THEMES`), so a capture of one theme never draws the other. Every platform checks that text fits
(its problems.txt) and reports a screen it didn't capture. Exits 1 when there are problems, 3 when no platform could
run.

`--only` takes canonical screen names; `--platforms` platform names or capture kinds. A sheet leaves out platforms
without the screen and the ones not captured in that language. `diff` keeps a 256-bit difference hash per image, so a
review looks at the sheets that changed (index.html opens on those) and the fit checks cover the rest. Needs Pillow:
when this Python lacks it, the macOS one (/usr/bin/python3) runs instead.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import avd  # noqa: E402
import companion  # noqa: E402
import sim  # noqa: E402
from sim import region  # noqa: E402,F401 (screens.region: the tools and tests name it here too)

THEMES = ("light", "dark")
KINDS = ("mac", "windows", "ios", "android")
VM_UNAVAILABLE = 3
DEFAULT_BASELINE = "accepted"
# What a screenshot simulator needs: accessibility on (the fit checks read the accessibility tree), and an iPad's apps
# full screen, with no Stage Manager windows or Split View. Each takes effect on the next boot.
SIMULATOR_DEFAULTS = (*sim.ACCESSIBILITY, ("com.apple.springboard", "SBChamoisWindowingEnabled", False),
                      ("com.apple.springboard", "SBMedusaMultitaskingEnabled", False))
# Row heights on a sheet, in pixels: desktop windows on top, devices below.
TOP_HEIGHT, BOTTOM_HEIGHT = 760, 960


def need_pillow() -> None:
    """Runs this file under /usr/bin/python3 (which has Pillow on macOS) when this Python lacks it."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        system = "/usr/bin/python3"
        if sys.executable != system and Path(system).exists() and subprocess.run(
                [system, "-c", "import PIL"], capture_output=True).returncode == 0:
            os.execv(system, [system, str(Path(__file__).resolve()), *sys.argv[1:]])
        sys.exit("screens: needs Pillow (python3 -m pip install Pillow)")


# The manifest.

@dataclass
class Platform:
    name: str
    capture: str
    settings: dict

    @property
    def label(self) -> str:
        return self.settings.get("label", self.name)

    @property
    def landscape(self) -> bool:
        default = "portrait" if self.capture in ("ios", "android") else "landscape"
        return self.settings.get("orientation", default) == "landscape"


@dataclass(frozen=True)
class Shot:
    name: str
    env: tuple = ()  # (name, value) pairs: iOS launch environment, Android intent extras
    camera: bool = False  # android: the emulator gets the host's webcam


class Screens:
    """companion.json's `screens`: the platforms, the screens and where everything goes."""

    def __init__(self, config: companion.Config):
        section = config.get("screens")
        if not section:
            raise companion.ConfigError(f"{companion.FILE} has no screens section (see companion.schema.json)")
        self.config = config
        self.root = config.root
        self.out = config.path("screens.out") or config.root / "build" / "screens"
        self.platforms = [Platform(name, value["capture"], value) for name, value in section["platforms"].items()]
        self.list: dict[str, dict] = section["list"]

    def shot(self, screen: str, platform: Platform) -> Shot | None:
        entries = self.list.get(screen, {})
        value = entries[platform.name] if platform.name in entries else entries.get(platform.capture)
        if value is None:
            return None
        if isinstance(value, str):
            return Shot(value)
        return Shot(value["shot"], tuple(sorted(value.get("env", {}).items())), value.get("camera", False))

    def pick_platforms(self, words: list[str] | None) -> list[Platform]:
        if not words:
            return list(self.platforms)
        unknown = [w for w in words if w not in KINDS and w not in {p.name for p in self.platforms}]
        if unknown:
            raise Usage(f"unknown platform {', '.join(unknown)} ({', '.join(p.name for p in self.platforms)}, "
                        f"or {', '.join(KINDS)})")
        return [p for p in self.platforms if p.name in words or p.capture in words]

    def pick_screens(self, words: list[str] | None) -> list[str]:
        if not words:
            return list(self.list)
        unknown = [w for w in words if w not in self.list]
        if unknown:
            raise Usage(f"unknown screen {', '.join(unknown)} (screens.list: {', '.join(self.list)})")
        return [s for s in self.list if s in words]

    def pick_languages(self, words: list[str] | None, default: list[str]) -> list[str]:
        if not words:
            return default
        if words == ["all"]:
            return self.config.languages
        unknown = [w for w in words if w not in self.config.languages]
        if unknown:
            raise Usage(f"unknown language {', '.join(unknown)} (languages.all, or all)")
        return words

    def folder(self, platform: Platform, language: str) -> Path:
        return self.out / platform.name / language

    def captured_languages(self) -> list[str]:
        """The languages some platform has captures for, in languages.all's order."""
        found = {p.parent.name for p in self.out.glob("*/*/*.png") if p.parent.parent.name in
                 {platform.name for platform in self.platforms}}
        return [language for language in self.config.languages if language in found]


class Usage(Exception):
    pass


def words(text: str | None) -> list[str] | None:
    return [w for w in re.split(r"[,\s]+", text) if w] if text else None


# Capture.

@dataclass
class Capture:
    screens: Screens
    platforms: list[Platform]
    languages: list[str]
    names: list[str]  # canonical screens
    themes: list[str]
    jobs: int
    build: bool
    cold: bool = False  # cold-boot the AVDs (and save their snapshots anew) instead of loading their snapshots
    problems: list[tuple[int, str, str]] = field(default_factory=list)  # (platform index, language, text)
    ran: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def say(self, text: str) -> None:
        with self.lock:
            print(text, flush=True)

    def problem(self, platform: Platform, language: str, text: str) -> None:
        with self.lock:
            self.problems.append((self.screens.platforms.index(platform), language, text))

    def log(self, name: str) -> Path:
        path = self.screens.out / "logs" / f"{name}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def sh(self, command: list, log: Path, env: dict | None = None, cwd: Path | None = None,
           timeout: float | None = None) -> int:
        """Runs `command` with its output appended to `log`, then the seconds it took; its exit code (124 when it
        outlasts `timeout` seconds)."""
        with log.open("a", encoding="utf8") as out:
            out.write(f"$ {' '.join(str(c) for c in command)}\n")
            out.flush()
            began = time.monotonic()
            try:
                code = subprocess.run([str(c) for c in command], stdout=out, stderr=subprocess.STDOUT,
                                      cwd=cwd or self.screens.root, env={**os.environ, **(env or {})},
                                      timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                out.write(f"(gave up after {timeout:.0f} s)\n")
                return 124
            out.write(f"{sim.took(began)}\n")
            return code

    def wanted(self, platform: Platform) -> list[tuple[str, Shot]]:
        return [(screen, shot) for screen in self.names if (shot := self.screens.shot(screen, platform))]

    def collect(self, platform: Platform, language: str, raw: Path,
                wanted: list[tuple[str, Shot]] | None = None) -> int:
        """Copies a run's shots (`<shot>-<theme>.png` in `raw`) to the canonical names; reports the missing ones, whose
        last shot stays (a failed run leaves the set it found). Only `wanted`'s when given (the shots of one iOS
        launch)."""
        folder = self.screens.folder(platform, language)
        folder.mkdir(parents=True, exist_ok=True)
        count = 0
        for screen, shot in self.wanted(platform) if wanted is None else wanted:
            for theme in self.themes:
                source, target = raw / f"{shot.name}-{theme}.png", folder / f"{screen}-{theme}.png"
                if source.is_file():
                    store(source, target, platform.settings.get("crop", False))
                    count += 1
                else:
                    kept = "; kept the last one" if target.is_file() else ""
                    self.problem(platform, language, f"no {source.name} for {screen}{kept}")
        return count

    def problems_from(self, platform: Platform, language: str, raw: Path, code: int, log: Path) -> None:
        """A run's problems.txt, or when it failed without one, the problem lines its log ends with."""
        listed = raw / "problems.txt"
        if listed.is_file() and listed.read_text("utf8").strip():
            for line in listed.read_text("utf8").splitlines():
                if line.strip():
                    self.problem(platform, language, line.strip())
            return
        if code == 0:
            return
        lines = log.read_text("utf8", errors="replace").splitlines()
        start = max((i for i, line in enumerate(lines) if line.startswith("Problems")), default=None)
        found = [line.strip() for line in lines[start + 1:] if line.startswith("  ")] if start is not None else []
        for line in found or [f"the run failed (exit {code}); see {log}"]:
            self.problem(platform, language, line)

    def run(self) -> int:
        groups = {kind: [p for p in self.platforms if p.capture == kind] for kind in KINDS}
        jobs = self.shares(groups)
        threads = []
        for platform in groups["mac"]:
            threads.append(threading.Thread(target=self.mac, args=(platform, jobs[platform.name])))
        for platform in groups["windows"]:
            threads.append(threading.Thread(target=self.windows, args=(platform, jobs[platform.name])))
        if groups["ios"]:
            threads.append(threading.Thread(target=self.ios, args=(groups["ios"], jobs["ios"])))
        if groups["android"]:
            threads.append(threading.Thread(target=self.android, args=(groups["android"], jobs["android"])))
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.write_problems(time.monotonic() - started)
        if not self.ran:
            return VM_UNAVAILABLE
        return 1 if self.problems else 0

    def shares(self, groups: dict[str, list[Platform]]) -> dict[str, int]:
        """`jobs` at once in all, shared among the platforms in proportion to their runs, at least one each: a Mac or
        Windows platform's languages (by its name), the iOS devices times the languages ("ios"), the Android emulators
        ("android")."""
        runs = {platform.name: len(self.languages) for kind in ("mac", "windows") for platform in groups[kind]}
        if groups["ios"]:
            runs["ios"] = len(groups["ios"]) * len(self.languages)
        if groups["android"]:
            runs["android"] = len(groups["android"])
        return sim.shares(self.jobs, runs) if runs else {}

    def write_problems(self, seconds: float) -> None:
        """problems.txt: this run's problems, in place of the lines of the platforms and languages it captured."""
        report = self.screens.out / "problems.txt"
        lines = [f"{self.screens.platforms[index].name} {language}: {text}"
                 for index, language, text in sorted(self.problems, key=lambda p: (p[0], p[1]))]
        header = (f"# screens.py capture: {', '.join(p.name for p in self.platforms)} in {' '.join(self.languages)}, "
                  f"{len(self.names)} screens, {', '.join(self.themes)} ({seconds / 60:.1f} min)")
        captured = {f"{platform.name} {language}:" for platform in self.platforms for language in self.languages}
        kept_headers, kept = [], []
        if report.is_file():
            for line in report.read_text("utf8").splitlines():
                if line.startswith("#"):
                    kept_headers.append(line)
                elif line and " ".join(line.split(" ", 2)[:2]) not in captured:
                    kept.append(line)
        order = {platform.name: index for index, platform in enumerate(self.screens.platforms)}
        merged = sorted([*kept, *lines], key=lambda line: (order.get(line.split(" ", 1)[0], len(order)),
                                                           line.split(" ", 2)[1] if " " in line else ""))
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join([*kept_headers[-4:], header, *merged]) + "\n", "utf8")
        if lines:
            print(f"{len(lines)} problems ({report}):", *[f"  {line}" for line in lines[:40]], sep="\n")
            if len(lines) > 40:
                print(f"  … and {len(lines) - 40} more")
        else:
            print(f"No problems ({report})")
        if kept:
            print(f"{len(kept)} more from earlier runs of other platforms or languages stay in it")

    # The Mac: one build, then each language hidden, `jobs` at a time (the platform's share of the budget).

    def mac(self, platform: Platform, jobs: int | None = None) -> None:
        jobs = self.jobs if jobs is None else jobs
        wanted = self.wanted(platform)
        if not wanted:
            return
        own = platform.settings.get("script")
        script = self.screens.root / own if own else TOOLS / "screenshots.sh"
        log = self.log(platform.name)
        log.write_text("", "utf8")
        if self.build:
            self.say(f"{platform.name}: building")
            if self.sh([script, "--build-only"], log):
                for language in self.languages:
                    self.problem(platform, language, f"the build failed; see {log}")
                return
        only = ",".join(dict.fromkeys(shot.name for _, shot in wanted))
        themes = ",".join(self.themes)

        def one(language: str) -> None:
            raw = self.screens.out / "raw" / platform.name / language
            shutil.rmtree(raw, ignore_errors=True)
            lang_log = self.log(f"{platform.name}-{language}")
            lang_log.write_text("", "utf8")
            code = self.sh([script, "--only", only, "--hidden", "--themes", themes, "--no-build", raw, language],
                           lang_log)
            self.problems_from(platform, language, raw, code, lang_log)
            count = self.collect(platform, language, raw)
            self.ran.add(platform.name)
            self.say(f"{platform.name} {language}: {count} shots")

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            list(pool.map(one, self.languages))

    # Windows, in the Parallels VM.

    def windows(self, platform: Platform, jobs: int | None = None) -> None:
        jobs = self.jobs if jobs is None else jobs
        wanted = self.wanted(platform)
        if not wanted:
            return
        vm = TOOLS / "windows" / "vm.sh"
        log = self.log(platform.name)
        log.write_text("", "utf8")
        only = ",".join(dict.fromkeys(shot.name for _, shot in wanted))
        themes = ",".join(self.themes)
        self.say(f"{platform.name}: in the VM ({log})")
        if platform.settings.get("vm", "check-layout") == "check-layout":
            code = self.sh([vm, "check-layout", *self.languages, "--", "-Only", only, "-Themes", themes, "-Jobs", str(jobs)],
                           log)
            if code == VM_UNAVAILABLE:
                self.unavailable(platform, log)
                return
            folder = self.screens.config.require_path("windows.dir") / "build" / "layout-check"
            for language in self.languages:
                self.problems_from(platform, language, folder / language, code, log)
                count = self.collect(platform, language, folder / language)
                self.say(f"{platform.name} {language}: {count} shots")
            self.ran.add(platform.name)
            return
        for language in self.languages:
            raw = self.screens.out / "raw" / platform.name / language
            shutil.rmtree(raw, ignore_errors=True)
            code = self.sh([vm, "screenshots", raw, "--", "-Language", language, "-Only", only, "-Themes", themes], log)
            if code == VM_UNAVAILABLE:
                self.unavailable(platform, log)
                return
            self.problems_from(platform, language, raw, code, log)
            count = self.collect(platform, language, raw)
            self.ran.add(platform.name)
            self.say(f"{platform.name} {language}: {count} shots")

    def unavailable(self, platform: Platform, log: Path) -> None:
        lines = [line for line in log.read_text("utf8").splitlines() if line.startswith("vm.sh:")]
        reason = lines[-1] if lines else "the VM is unavailable"
        self.say(f"{platform.name}: skipped, {reason}")
        for language in self.languages:
            self.problem(platform, language, f"not captured: {reason}")

    # iOS: one build, then a simulator of its own per device, side by side; with more than one language, clones of it.

    def ios(self, platforms: list[Platform], jobs: int | None = None) -> None:
        jobs = self.jobs if jobs is None else jobs
        log = self.log("ios")
        log.write_text("", "utf8")
        config = self.screens.config
        derived = sim.derived_data(config)
        try:
            if self.build:
                self.say("ios: building")
                app = sim.build(config, derived, log)
            elif (app := sim.built_app(config, derived)) is None:
                raise RuntimeError(f"no iOS build in {derived}")
        except RuntimeError as error:
            for platform in platforms:
                for language in self.languages:
                    self.problem(platform, language, str(error))
            return
        bundle = plistlib.loads((app / "Info.plist").read_bytes())["CFBundleIdentifier"]
        platforms = [platform for platform in platforms if self.wanted(platform)]
        # `jobs` simulators in all, at least one per device: a device with more runs its languages on clones.
        workers = sim.shares(jobs, {platform.name: len(self.languages) for platform in platforms})
        threads = [threading.Thread(target=self.device, args=(platform, app, bundle)) for platform in platforms
                   if workers[platform.name] == 1]
        cloned = [platform for platform in platforms if workers[platform.name] > 1]
        if cloned:
            threads.append(threading.Thread(target=self.clones, args=(cloned, workers, app, bundle)))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def simulator_name(self, platform: Platform) -> str:
        return f"{self.app_name} screens {platform.name}"

    @property
    def app_name(self) -> str:
        return self.screens.config.get("name", self.screens.root.name)

    def device(self, platform: Platform, app: Path, bundle: str) -> None:
        """One device's simulator, kept between runs: boots it once and runs every language on it."""
        log = self.log(platform.name)
        log.write_text("", "utf8")
        try:
            phone = sim.Simulator(simulator(self.simulator_name(platform), platform.settings.get("device"), log), log)
            self.say(f"{platform.name}: booting {platform.settings.get('device')}")
            phone.boot_with(SIMULATOR_DEFAULTS)
            phone.install(app)
        except RuntimeError as error:
            for language in self.languages:
                self.problem(platform, language, f"{error}; see {log}")
            return
        for language in self.languages:
            self.on_simulator(phone, platform, bundle, language)
        phone.shutdown()

    def clones(self, platforms: list[Platform], workers: dict[str, int], app: Path, bundle: str) -> None:
        """The devices whose languages run side by side: clones of each one's simulator (sim.on_clones), each booted
        once (the defaults come with the clone), the app installed, then a language at a time until none are left."""
        groups = []
        sim.clean_stale_clones(self.app_name)
        for platform in platforms:
            log = self.log(platform.name)
            log.write_text("", "utf8")
            try:
                template = sim.Simulator(simulator(self.simulator_name(platform), platform.settings.get("device"), log),
                                         log)
                template.shutdown()  # a simulator clones only when shut down
            except RuntimeError as error:
                for language in self.languages:
                    self.problem(platform, language, f"{error}; see {log}")
                continue
            self.say(f"{platform.name}: {workers[platform.name]} clones of {platform.settings.get('device')}")
            tasks = [(platform, language) for language in self.languages]
            groups.append(sim.Clones(template.udid, self.simulator_name(platform), tasks, workers[platform.name], log))

        def prepare(clone: sim.Simulator) -> None:
            clone.boot_with(SIMULATOR_DEFAULTS)
            clone.install(app)

        def failed(task: tuple[Platform, str], error: Exception) -> None:
            platform, language = task
            logs = self.log(platform.name).parent
            self.problem(platform, language, f"the simulator didn't start ({error}); see {logs}/{platform.name}-*.log")

        sim.on_clones(groups, prepare, lambda clone, task: self.on_simulator(clone, task[0], bundle, task[1]), failed,
                      note=lambda line: self.say(f"ios: {line}"))

    def on_simulator(self, phone: sim.Simulator, platform: Platform, bundle: str, language: str) -> None:
        """One language on a booted simulator with the app installed: one launch draws every shot asked for (in the
        themes asked for, `LACHING_SCREENSHOT_THEMES`), and a shot with an env of its own gets a launch with it."""
        launches: dict[tuple, list[tuple[str, Shot]]] = {}
        for screen, shot in self.wanted(platform):
            launches.setdefault(shot.env, []).append((screen, shot))
        mode = self.screens.config.get("mac.screenshotEnv") or "LACHING_SCREENSHOT_MODE"
        began = time.monotonic()
        count = 0
        for number, (env, wanted) in enumerate(launches.items(), 1):
            raw = self.screens.out / "raw" / platform.name / language / str(number)
            try:
                phone.run(bundle, language, {mode: ",".join(dict.fromkeys(shot.name for _, shot in wanted)),
                                             "LACHING_SCREENSHOT_THEMES": ",".join(self.themes), **dict(env)}, raw)
            except RuntimeError as error:
                self.problem(platform, language, f"{error}; see {phone.log}")
                continue
            self.problems_from(platform, language, raw, 0, phone.log)
            count += self.collect(platform, language, raw, wanted)
        self.ran.add(platform.name)
        self.say(f"{platform.name} {language}: {count} shots, {time.monotonic() - began:.0f} s")

    def android(self, platforms: list[Platform], jobs: int | None = None) -> None:
        jobs = self.jobs if jobs is None else jobs
        log = self.log("android")
        log.write_text("", "utf8")
        config = self.screens.config
        try:
            if self.build:
                self.say("android: building")
                command, cwd = avd.gradle(config)
                if self.sh(command, log, cwd=cwd, timeout=1800):
                    raise RuntimeError(f"the Android build failed; see {log}")
            apk, app_id = avd.apk(config), avd.app_id(config)
        except (RuntimeError, companion.ConfigError) as error:
            for platform in platforms:
                for language in self.languages:
                    self.problem(platform, language, str(error))
            return
        running = threading.BoundedSemaphore(max(1, jobs))  # emulators at once: the budget's share

        def bounded(platform: Platform, index: int) -> None:
            with running:
                self.emulator(platform, apk, app_id, index)

        threads = [threading.Thread(target=bounded, args=(platform, index))
                   for index, platform in enumerate(platforms) if self.wanted(platform)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def emulator(self, platform: Platform, apk: Path, app_id: str, index: int) -> None:
        """One platform's AVD: boots it once and runs every language on it, each a launch per distinct shot env."""
        log = self.log(platform.name)
        log.write_text("", "utf8")
        name = platform.settings.get("avd")
        if not name:
            for language in self.languages:
                self.problem(platform, language, "no avd in screens.platforms (an AVD name, avdmanager list avd)")
            return
        wanted = self.wanted(platform)
        try:
            emulator = avd.Emulator(name, index, log, camera=any(shot.camera for _, shot in wanted), cold=self.cold)
            self.say(f"{platform.name}: booting {name}")
            emulator.boot()
            emulator.install(apk)
            component = emulator.launcher(app_id)
            if posture := platform.settings.get("posture"):
                emulator.posture(posture)
            if "orientation" in platform.settings:
                emulator.orientation(platform.landscape)
        except (RuntimeError, OSError) as error:
            for language in self.languages:
                self.problem(platform, language, str(error))
            if "emulator" in locals():
                emulator.kill()
            return
        launches: dict[tuple, list[tuple[str, Shot]]] = {}
        for screen, shot in wanted:
            launches.setdefault(shot.env, []).append((screen, shot))
        try:
            for language in self.languages:
                count = 0
                for number, (env, shots) in enumerate(launches.items(), 1):
                    raw = self.screens.out / "raw" / platform.name / language / str(number)
                    names = list(dict.fromkeys(shot.name for _, shot in shots))
                    try:
                        emulator.run(app_id, component, language, region(language).partition("_")[2], names, dict(env),
                                     raw)
                    except (RuntimeError, OSError) as error:
                        self.problem(platform, language, f"{error}; see {log}")
                        continue
                    self.problems_from(platform, language, raw, 0, log)
                    count += self.collect(platform, language, raw, shots)
                self.ran.add(platform.name)
                self.say(f"{platform.name} {language}: {count} shots")
        finally:
            emulator.kill()


def simulator(name: str, device: str | None, log: Path | None = None) -> str:
    """The UDID of the simulator called `name`, made the first time from `device` on the newest iOS runtime that runs it
    with SIMULATOR_DEFAULTS written (booted once to write them, then shut down)."""
    if not device:
        raise RuntimeError("no device in screens.platforms (a simulator device type)")
    for found in sim.devices():
        if found["name"] == name and found.get("isAvailable", True):
            return found["udid"]
    runtime = sim.newest_runtime(device, sim.runtimes())
    if runtime is None:
        raise RuntimeError(f"no installed iOS simulator runtime supports {device} (Xcode → Settings → Components)")
    try:
        made = sim.Simulator.create(name, device, runtime["identifier"], log)
        made.boot()
        for domain, key, value in SIMULATOR_DEFAULTS:
            made.write_default(domain, key, value)
        made.shutdown()
        return made.udid
    except RuntimeError as error:
        raise RuntimeError(f"couldn't make a {device} simulator: {error}") from None


def store(source: Path, target: Path, crop: bool) -> None:
    if not crop:
        shutil.copyfile(source, target)
        return
    from PIL import Image

    with Image.open(source) as image:
        cropped = crop_to_window(image)
        if cropped is image:
            shutil.copyfile(source, target)
        else:
            cropped.save(target)


def crop_to_window(image):
    """The window in a shot: without the transparent margin around it, or the flat one in the corner pixel's color."""
    from PIL import Image, ImageChops

    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        mask = image.convert("RGBA").getchannel("A").point(lambda a: 255 if a > 8 else 0)
    else:
        rgb = image.convert("RGB")
        flat = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        mask = ImageChops.difference(rgb, flat).convert("L").point(lambda v: 255 if v > 8 else 0)
    box = mask.getbbox()
    if not box or box == (0, 0, *image.size):
        return image
    return image.crop(box)


# Compare: the sheets and the index.

def font(size: int):
    from PIL import ImageFont

    for path in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


SHEET = {"light": {"paper": (236, 236, 236), "ink": (40, 40, 40), "muted": (120, 120, 120)},
         "dark": {"paper": (34, 34, 34), "ink": (230, 230, 230), "muted": (140, 140, 140)}}


def sheet(screens: Screens, screen: str, language: str, theme: str):
    """The sheet for one screen, language and theme, or None when no platform has a capture of it."""
    from PIL import Image, ImageDraw

    rows: dict[bool, list] = {True: [], False: []}
    any_image = False
    for platform in screens.platforms:
        shot = screens.shot(screen, platform)
        folder = screens.folder(platform, language)
        if shot is None or not folder.is_dir():
            continue
        path = folder / f"{screen}-{theme}.png"
        image = Image.open(path).convert("RGB") if path.is_file() else None
        any_image = any_image or image is not None
        rows[platform.landscape].append((platform, shot, image))
    if not any_image:
        return None
    colors, label_font, small_font = SHEET[theme], font(30), font(22)
    gap, label = 32, 76
    laid: list[tuple[int, list]] = []
    for landscape, height in ((True, TOP_HEIGHT), (False, BOTTOM_HEIGHT)):
        tiles = []
        for platform, shot, image in rows[landscape]:
            if image is None:
                tiles.append((platform, shot, None, (round(height * 0.5), height)))
                continue
            scale = height / image.height
            tiles.append((platform, shot, image, (max(1, round(image.width * scale)), height)))
        if tiles:
            laid.append((height, tiles))
    width = max(sum(size[0] for *_, size in tiles) + gap * (len(tiles) + 1) for _, tiles in laid)
    total = sum(height + label + gap for height, _ in laid) + gap
    canvas = Image.new("RGB", (width, total), colors["paper"])
    draw = ImageDraw.Draw(canvas)
    y = gap
    for height, tiles in laid:
        x = gap
        for platform, shot, image, size in tiles:
            draw.text((x, y), platform.label, fill=colors["ink"], font=label_font)
            note = f"{shot.name}" + (f"  {image.width}×{image.height}" if image else "  missing")
            draw.text((x, y + 38), note, fill=colors["muted"], font=small_font)
            top = y + label
            if image is None:
                draw.rectangle((x, top, x + size[0], top + size[1]), outline=colors["muted"], width=3)
                draw.line((x, top, x + size[0], top + size[1]), fill=colors["muted"], width=3)
            else:
                canvas.paste(image.resize(size, Image.LANCZOS), (x, top))
            x += size[0] + gap
        y += height + label + gap
    return canvas


def compare(screens: Screens, languages: list[str], names: list[str], themes: list[str]) -> int:
    count = 0
    for language in languages:
        for screen in names:
            for theme in themes:
                target = screens.out / "sheets" / language / f"{screen}-{theme}.jpg"
                made = sheet(screens, screen, language, theme)
                if made is None:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                made.save(target, quality=90, subsampling=0)
                count += 1
    index = write_index(screens)
    print(f"{count} sheets in {screens.out / 'sheets'}; open {index}")
    return 0 if count else 1


def write_index(screens: Screens) -> Path:
    """index.html: every sheet there is, to flip through; with a diff's changes.json, the changed ones first."""
    sheets = screens.out / "sheets"
    found = sorted(p.relative_to(screens.out).as_posix() for p in sheets.glob("*/*.jpg")) if sheets.is_dir() else []
    order = {name: index for index, name in enumerate(screens.list)}
    languages = [lang for lang in screens.config.languages if (sheets / lang).is_dir()]
    names = sorted({Path(p).stem.rsplit("-", 1)[0] for p in found}, key=lambda n: order.get(n, len(order)))
    changes_file = screens.out / "changes.json"
    changes = json.loads(changes_file.read_text("utf8")) if changes_file.is_file() else None
    changed = sorted({f"{lang}/{screen}" for lang, screen in changed_sheets(changes)}) if changes else []
    problems_file = screens.out / "problems.txt"
    problems = problems_file.read_text("utf8").splitlines() if problems_file.is_file() else []
    data = {"sheets": found, "languages": languages, "screens": names, "changed": changed,
            "against": changes.get("against") if changes else None,
            "problems": [line for line in problems if line and not line.startswith("#")]}
    page = INDEX.replace("/*DATA*/", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    target = screens.out / "index.html"
    target.write_text(page.replace("<!--TITLE-->", html.escape(f"{screens.root.name} screens")), "utf8")
    return target


INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><!--TITLE--></title>
<style>
  :root { --paper: #f4f4f4; --ink: #222; --muted: #777; --line: #ccc; --accent: #c2185b; color-scheme: light dark; }
  @media (prefers-color-scheme: dark) { :root { --paper: #1b1b1b; --ink: #eee; --muted: #999; --line: #444; --accent: #f06292; } }
  body { margin: 0; background: var(--paper); color: var(--ink); font: 14px/1.4 -apple-system, "Segoe UI", sans-serif; }
  header { position: sticky; top: 0; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; padding: 10px 16px;
           background: var(--paper); border-bottom: 1px solid var(--line); z-index: 1; }
  select, button, label { font: inherit; color: inherit; }
  select, button { background: transparent; border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; }
  .muted { color: var(--muted); }
  .changed { color: var(--accent); font-weight: 600; }
  main { padding: 16px; }
  main img { max-width: 100%; height: auto; display: block; cursor: zoom-in; }
  main img.full { max-width: none; cursor: zoom-out; }
  details { padding: 0 16px; }
  pre { white-space: pre-wrap; font-size: 12px; }
</style>
</head>
<body>
<header>
  <button id="prev" title="Previous screen (←)">←</button>
  <select id="screen"></select>
  <button id="next" title="Next screen (→)">→</button>
  <select id="language" title="Language (↑ ↓)"></select>
  <button id="theme" title="Light or dark (t)"></button>
  <label><input type="checkbox" id="only"> changed only</label>
  <span id="status" class="muted"></span>
</header>
<details id="problems"><summary></summary><pre></pre></details>
<main><img id="sheet" alt=""></main>
<script>
const data = /*DATA*/;
const $ = (id) => document.getElementById(id);
const has = new Set(data.sheets);
const changed = new Set(data.changed);
let state = { screen: data.screens[0], language: data.languages[0], theme: "light", only: changed.size > 0 };
try { Object.assign(state, JSON.parse(sessionStorage.getItem("screens") || "{}")); } catch (e) {}
function screens() {
  return data.screens.filter((s) => !state.only || changed.has(`${state.language}/${s}`));
}
function render() {
  const list = screens();
  if (!list.includes(state.screen)) state.screen = list[0] || data.screens[0];
  $("screen").innerHTML = list.map((s) => `<option${changed.has(`${state.language}/${s}`) ? ' class="changed"' : ""}>${s}</option>`).join("");
  $("screen").value = state.screen;
  $("language").innerHTML = data.languages.map((l) => `<option>${l}</option>`).join("");
  $("language").value = state.language;
  $("theme").textContent = state.theme;
  $("only").checked = state.only;
  $("only").disabled = changed.size === 0;
  const path = `sheets/${state.language}/${state.screen}-${state.theme}.jpg`;
  $("sheet").src = has.has(path) ? path : "";
  $("sheet").alt = has.has(path) ? `${state.screen} in ${state.language}, ${state.theme}` : "No sheet for this screen";
  const position = `${list.indexOf(state.screen) + 1} of ${list.length}`;
  const against = data.against ? ` · ${changed.size} changed since “${data.against}”` : "";
  $("status").textContent = position + against;
  try { sessionStorage.setItem("screens", JSON.stringify(state)); } catch (e) {}
}
function step(by) {
  const list = screens();
  state.screen = list[(list.indexOf(state.screen) + by + list.length) % list.length];
  render();
}
function language(by) {
  const i = data.languages.indexOf(state.language);
  state.language = data.languages[(i + by + data.languages.length) % data.languages.length];
  render();
}
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("screen").onchange = (e) => { state.screen = e.target.value; render(); };
$("language").onchange = (e) => { state.language = e.target.value; render(); };
$("theme").onclick = () => { state.theme = state.theme === "light" ? "dark" : "light"; render(); };
$("only").onchange = (e) => { state.only = e.target.checked; render(); };
$("sheet").onclick = (e) => e.target.classList.toggle("full");
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT") return;
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "ArrowUp") { e.preventDefault(); language(-1); }
  else if (e.key === "ArrowDown") { e.preventDefault(); language(1); }
  else if (e.key === "t") $("theme").click();
});
$("problems").hidden = data.problems.length === 0;
$("problems").querySelector("summary").textContent = `${data.problems.length} problems in the last capture`;
$("problems").querySelector("pre").textContent = data.problems.join("\\n");
render();
</script>
</body>
</html>
"""


# Diff: perceptual hashes against an accepted run.

def dhash(image, size: int = 16) -> int:
    """A difference hash: size×size bits, each whether a pixel of the shrunken grey image is brighter than the next."""
    from PIL import Image

    grey = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    pixels = list(grey.getdata())
    bits = 0
    for row in range(size):
        for column in range(size):
            left, right = pixels[row * (size + 1) + column], pixels[row * (size + 1) + column + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def hashes(screens: Screens) -> dict[str, dict]:
    from PIL import Image

    found = {}
    names = {p.name for p in screens.platforms}
    for path in sorted(screens.out.glob("*/*/*.png")):
        if path.parent.parent.name not in names:
            continue
        with Image.open(path) as image:
            found[path.relative_to(screens.out).as_posix()] = {"hash": f"{dhash(image):064x}", "size": list(image.size)}
    return found


def baseline_path(screens: Screens, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise Usage(f"a baseline name is letters, digits, dots, dashes and underscores, not {name!r}")
    return screens.out / "baselines" / f"{name}.json"


def accept(screens: Screens, name: str) -> int:
    """Records the captures as they are under `name`, over what it held for the same files."""
    path = baseline_path(screens, name)
    current = hashes(screens)
    if not current:
        print(f"No captures in {screens.out}; run capture first", file=sys.stderr)
        return 1
    kept = json.loads(path.read_text("utf8"))["images"] if path.is_file() else {}
    kept.update(current)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"images": kept}, indent=1, sort_keys=True) + "\n", "utf8")
    (screens.out / "changes.json").unlink(missing_ok=True)
    write_index(screens)
    print(f"{len(current)} captures accepted as {name!r} ({path})")
    return 0


def diff(screens: Screens, name: str, threshold: int) -> int:
    path = baseline_path(screens, name)
    if not path.is_file():
        print(f"No baseline {name!r} yet: `screens.py accept {name}` records the captures as they are", file=sys.stderr)
        return 1
    accepted = json.loads(path.read_text("utf8"))["images"]
    current = hashes(screens)
    changed, new = [], []
    for rel, now in current.items():
        then = accepted.get(rel)
        if then is None:
            new.append(rel)
        elif then["size"] != now["size"] or distance(int(then["hash"], 16), int(now["hash"], 16)) > threshold:
            changed.append(rel)
    captured = {str(Path(rel).parent) for rel in current}
    gone = sorted(rel for rel in accepted if str(Path(rel).parent) in captured and rel not in current)
    changes = {"against": name, "threshold": threshold, "changed": changed, "new": new, "gone": gone}
    (screens.out / "changes.json").write_text(json.dumps(changes, indent=1) + "\n", "utf8")
    index = write_index(screens)
    for label, items in (("Changed", changed), ("New", new), ("Gone", gone)):
        if items:
            print(f"{label} ({len(items)}):", *[f"  {item}" for item in items], sep="\n")
    sheets = len(changed_sheets(changes))
    print(f"{len(changed)} changed, {len(new)} new, {len(gone)} gone since {name!r}: {sheets} sheets to look at in {index}")
    return 0


def changed_sheets(changes: dict) -> set[tuple[str, str]]:
    """(language, screen) for each sheet with an image that changed, came or went."""
    found = set()
    for rel in [*changes.get("changed", []), *changes.get("new", []), *changes.get("gone", [])]:
        _, language, file = rel.split("/")
        found.add((language, Path(file).stem.rsplit("-", 1)[0]))
    return found


# The command line.

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="screens.py", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="capture the screens on each platform, side by side")
    capture.add_argument("--platforms",
                         help="platform names or capture kinds (mac, windows, ios, android), comma-separated")
    capture.add_argument("--langs", help="languages, comma-separated, or all (default: the source language)")
    capture.add_argument("--only", help="canonical screen names, comma-separated (default: every one)")
    capture.add_argument("--themes", default="light,dark", help="light, dark or both (default), in that order")
    capture.add_argument("--jobs", type=int, default=int(os.environ.get("LAYOUT_JOBS", 3)),
                         help="app processes, simulators and emulators at once in all, shared among the platforms, "
                              "at least one each (LAYOUT_JOBS, 3)")
    capture.add_argument("--no-build", action="store_true", help="run the last builds")
    capture.add_argument("--cold", action="store_true", help="cold-boot the Android emulators and save their snapshots anew")
    sheets = commands.add_parser("compare", help="one sheet per screen, language and theme, and index.html")
    sheets.add_argument("--langs", help="languages, comma-separated, or all (default: the captured ones)")
    sheets.add_argument("--only", help="canonical screen names, comma-separated")
    sheets.add_argument("--themes", default="light,dark")
    differ = commands.add_parser("diff", help="the captures that changed since an accepted run")
    differ.add_argument("--against", default=DEFAULT_BASELINE, help=f"the baseline's name ({DEFAULT_BASELINE})")
    differ.add_argument("--threshold", type=int, default=4, help="bits of 256 that may differ (4)")
    accepting = commands.add_parser("accept", help="record the captures as they are as a baseline")
    accepting.add_argument("name", nargs="?", default=DEFAULT_BASELINE)
    args = parser.parse_args(argv)
    try:
        screens = Screens(companion.load())
        if args.command in ("capture", "compare"):
            themes = words(args.themes) or list(THEMES)
            if any(theme not in THEMES for theme in themes):
                raise Usage(f"--themes takes {' and '.join(THEMES)}")
            names = screens.pick_screens(words(args.only))
        if args.command == "capture":
            platforms = screens.pick_platforms(words(args.platforms))
            languages = screens.pick_languages(words(args.langs), [screens.config.source_language])
            return Capture(screens, platforms, languages, names, themes, args.jobs, not args.no_build, args.cold).run()
        if args.command == "compare":
            languages = screens.pick_languages(words(args.langs), screens.captured_languages())
            return compare(screens, languages, names, themes)
        if args.command == "diff":
            return diff(screens, args.against, args.threshold)
        return accept(screens, args.name)
    except Usage as mistake:
        print(f"screens: {mistake}", file=sys.stderr)
        return 2
    except companion.ConfigError as error:
        print(f"screens: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    companion.utf8_console()
    need_pillow()
    sys.exit(main(sys.argv[1:]))
