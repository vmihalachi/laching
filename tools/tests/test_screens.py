#!/usr/bin/env python3
"""Tests for tools/screens.py: the manifest, the canonical captures, the sheets and the perceptual diff."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import avd  # noqa: E402
import companion  # noqa: E402
import layoutcheck  # noqa: E402
import screens  # noqa: E402
import sim  # noqa: E402
from support import FixtureAppTestCase  # noqa: E402

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
NO_PILLOW = "needs Pillow (the macOS /usr/bin/python3 has it)"


def manifest(root: Path) -> screens.Screens:
    return screens.Screens(companion.load(root))


class ManifestTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.screens = manifest(self.root)
        self.mac, self.windows, self.phone = self.screens.platforms
        devices = mock.patch.object(sim, "DEVICES", self.root / "devices")
        devices.start()
        self.addCleanup(devices.stop)

    def written(self, *udids):
        """The simulators `udids` with screens.py's defaults in their preference files, as a first run leaves them."""
        for udid in udids:
            preferences = sim.DEVICES / udid / "data" / "Library" / "Preferences"
            preferences.mkdir(parents=True)
            for domain in {domain for domain, _, _ in screens.SIMULATOR_DEFAULTS}:
                values = {key: value for d, key, value in screens.SIMULATOR_DEFAULTS if d == domain}
                (preferences / f"{domain}.plist").write_bytes(screens.plistlib.dumps({"Other": 1, **values}))

    def test_each_platform_gets_its_shot_by_name_or_by_capture_kind(self):
        self.assertEqual(self.screens.shot("home", self.phone), screens.Shot("today"))
        self.assertEqual(self.screens.shot("settings", self.phone), screens.Shot("settings", (("FIXTURE_TWO", "1"),)))
        self.assertIsNone(self.screens.shot("settings", self.windows), "null: not on that platform")
        self.assertIsNone(self.screens.shot("panel", self.phone), "absent: not on that platform")
        self.assertEqual(self.screens.out, self.root / "build" / "screens")
        self.assertEqual(self.windows.label, "Windows")
        self.assertTrue(self.mac.landscape)
        self.assertFalse(self.phone.landscape, "iOS goes in the portrait row")

    def test_picking_platforms_screens_and_languages(self):
        self.assertEqual(self.screens.pick_platforms(["ios"]), [self.phone])
        self.assertEqual(self.screens.pick_platforms(["windows", "mac"]), [self.mac, self.windows], "manifest order")
        self.assertEqual(self.screens.pick_screens(["settings", "panel"]), ["panel", "settings"])
        self.assertEqual(self.screens.pick_languages(["all"], ["en"]), ["en", "it", "de"])
        self.assertEqual(self.screens.pick_languages(None, ["en"]), ["en"])
        for pick, argument in ((self.screens.pick_platforms, ["linux"]), (self.screens.pick_screens, ["nope"]),
                               (self.screens.pick_languages, ["xx"])):
            with self.subTest(argument=argument), self.assertRaises(screens.Usage):
                pick(argument) if pick != self.screens.pick_languages else pick(argument, [])

    def test_a_simulator_region_for_each_language(self):
        self.assertEqual(screens.region("en"), "en_US")
        self.assertEqual(screens.region("it"), "it_IT")
        self.assertEqual(screens.region("pt-BR"), "pt_BR")
        self.assertEqual(screens.region("zh-Hant"), "zh_TW")
        self.assertEqual(screens.words("en, fr,de"), ["en", "fr", "de"])

    def test_a_simulator_is_made_on_the_newest_runtime_that_runs_the_device(self):
        runtimes = {"runtimes": [
            {"platform": "iOS", "isAvailable": True, "version": "27.0", "identifier": "ios-27-0",
             "supportedDeviceTypes": [{"name": "iPhone 17 Pro", "identifier": "type.iPhone-17-Pro"}]},
            {"platform": "iOS", "isAvailable": True, "version": "27.1", "identifier": "ios-27-1",
             "supportedDeviceTypes": [{"name": "iPhone Duo", "identifier": "type.iPhone-Duo"}]},
        ]}
        devices = {"devices": {"ios-27-1": [{"name": "app screens duo", "udid": "DUO", "isAvailable": True}]}}
        calls = []

        def run(command, **_):
            calls.append(command)
            listing = {"devices": devices, "runtimes": runtimes}.get(command[3])
            return subprocess.CompletedProcess(command, 0, json.dumps(listing) if listing else "NEW\n", "")

        with mock.patch.object(screens.subprocess, "run", run):
            self.assertEqual(screens.simulator("app screens duo", "iPhone Duo"), "DUO", "already made")
            calls.clear()
            self.assertEqual(screens.simulator("app screens iphone", "iPhone 17 Pro"), "NEW")
            self.assertEqual(calls[2], ["xcrun", "simctl", "create", "app screens iphone", "iPhone 17 Pro", "ios-27-0"])
            # Made with its defaults written, once: a later run boots it once.
            self.assertEqual([c[2] for c in calls[3:]], ["boot", "bootstatus", "spawn", "spawn", "spawn", "spawn",
                                                          "shutdown"])
            self.assertEqual(calls[-2][-4:], ["com.apple.springboard", "SBMedusaMultitaskingEnabled", "-bool", "false"])
            with self.assertRaisesRegex(RuntimeError, "supports iPad Pro"):
                screens.simulator("app screens ipad", "iPad Pro")

    def test_the_simulator_build_shares_the_compilation_cache_unless_turned_off(self):
        derived = self.root / "derived"
        config = self.screens.config
        config.data["mac"]["iosScheme"] = "FixtureiOS"
        commands = []

        def run(command, **_):
            commands.append(command)
            (derived / "Build" / "Products" / "Debug-iphonesimulator" / "Fixture.app").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(sim.subprocess, "run", run):
            sim.build(config, derived)
            with mock.patch.dict(sim.companion.os.environ, {"LACHING_COMPILATION_CACHE": "0"}):
                sim.build(config, derived)
        self.assertEqual(commands[0][-len(companion.xcode_cache_settings(mapped=True)):],
                         companion.xcode_cache_settings(mapped=True))
        self.assertIn("CLANG_ENABLE_PREFIX_MAPPING=YES", commands[0])
        self.assertFalse([a for a in commands[1] if a.startswith(("COMPILATION_CACHE", "SWIFT_", "CLANG_"))])

    def test_ios_takes_each_language_in_one_launch_and_a_shot_with_its_own_env_in_another(self):
        capture = screens.Capture(self.screens, [self.phone], ["en"], ["home", "settings"], ["light"], 1, False)
        drawn = self.root / "container"
        launches = []
        simctl = []

        def run(command, env=None, **_):
            if command[2] != "launch":  # boot, install…
                simctl.append(command[2:])
                return subprocess.CompletedProcess(command, 0, "", "")
            mode = env["SIMCTL_CHILD_LACHING_SCREENSHOT_MODE"]
            launches.append((mode, env.get("SIMCTL_CHILD_FIXTURE_TWO"), command[-4:]))
            folder = drawn / str(len(launches))
            folder.mkdir(parents=True)
            for theme in ("light", "dark"):
                (folder / f"{mode}-{theme}.png").write_bytes(b"png")
            if mode == "settings":
                (folder / "problems.txt").write_text("settings-light.png: a label ends in …\n", "utf8")
            return subprocess.CompletedProcess(command, 0, f"app.fixture: 42\r\nReady\r\n{folder}\r\n", "")

        self.written("UDID")
        with mock.patch.object(screens, "simulator", return_value="UDID"), \
                mock.patch.object(screens.sim.subprocess, "run", run), redirect_stdout(io.StringIO()):
            capture.device(self.phone, self.root / "Fixture.app", "app.fixture")
        self.assertEqual(launches, [("today", None, ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]),
                                    ("settings", "1", ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"])])
        folder = self.screens.out / "phone" / "en"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["home-light.png", "settings-light.png"])
        self.assertEqual(capture.problems, [(2, "en", "settings-light.png: a label ends in …")])
        self.assertEqual(capture.ran, {"phone"})
        self.assertFalse((drawn / "1").exists(), "moved out of the app's container")
        self.assertEqual([c[0] for c in simctl].count("boot"), 1, "the defaults are set: no second boot")
        self.assertNotIn("spawn", [c[0] for c in simctl], "read from the preference files, not the simulator")
        log = (self.screens.out / "logs" / "phone.log").read_text("utf8")
        self.assertRegex(log, r"\$ xcrun simctl boot UDID \(\d+\.\d s\)", "each command with its seconds")
        self.assertRegex(log, r"\n\(\d+\.\d s\)\n", "a launch's seconds after its output")

    def test_ios_writes_the_missing_defaults_and_boots_again(self):
        simctl = []

        def run(command, **_):
            simctl.append(command[2:])
            return subprocess.CompletedProcess(command, 0, "", "")

        self.written("SET")
        preferences = sim.DEVICES / "SET" / "data" / "Library" / "Preferences" / "com.apple.springboard.plist"
        self.assertTrue(sim.Simulator("SET").defaults_set(screens.SIMULATOR_DEFAULTS))
        preferences.write_bytes(screens.plistlib.dumps({"SBChamoisWindowingEnabled": True}))
        self.assertFalse(sim.Simulator("SET").defaults_set(screens.SIMULATOR_DEFAULTS), "turned back on")
        with mock.patch.object(sim.subprocess, "run", run):
            self.assertTrue(sim.Simulator("NEW").boot_with(screens.SIMULATOR_DEFAULTS), "no preference files yet")
        self.assertEqual([c[0] for c in simctl], ["boot", "bootstatus", *["spawn"] * 4, "shutdown", "boot", "bootstatus"])
        self.assertEqual([c[3:] for c in simctl if c[0] == "spawn"][2:],
                         [["write", "com.apple.springboard", key, "-bool", "false"]
                          for key in ("SBChamoisWindowingEnabled", "SBMedusaMultitaskingEnabled")])

    def test_ios_runs_the_languages_side_by_side_on_clones_of_each_devices_simulator(self):
        capture = screens.Capture(self.screens, [self.phone], ["en", "it", "de"], ["home"], ["light"], 2, False)
        app = self.root / "macos" / "build" / "ios-simulator.noindex" / "Build" / "Products" / "Debug-iphonesimulator"
        (app / "Fixture.app").mkdir(parents=True)
        (app / "Fixture.app" / "Info.plist").write_bytes(screens.plistlib.dumps({"CFBundleIdentifier": "app.fixture"}))
        drawn = self.root / "container"
        lock = screens.threading.Lock()
        devices = [{"name": "fixture screens phone", "udid": "PHONE", "isAvailable": True, "state": "Booted"},
                   {"name": "fixture screens phone clone #1 (999999999)", "udid": "STALE", "isAvailable": True}]
        calls, launches = [], []
        second = screens.threading.Event()  # the first launch waits for one on the other clone: none takes them all

        def run(command, env=None, **_):
            with lock:
                calls.append(command[2:])
                if command[2:4] == ["list", "devices"]:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"devices": {"ios": devices}}), "")
                if command[2] == "clone":  # its data with the kept simulator's defaults
                    self.written(made := f"CLONE{command[-1].split('#')[1][0]}")
                    return subprocess.CompletedProcess(command, 0, f"{made}\n", "")
                if command[2] != "launch":
                    return subprocess.CompletedProcess(command, 0, "", "")
                launches.append((command[5], command[-3].strip("()")))
                folder = drawn / str(len(launches))
            if folder.name == "1":
                second.wait(10)
            elif folder.name == "2":
                second.set()
            folder.mkdir(parents=True)
            (folder / "today-light.png").write_bytes(b"png")
            return subprocess.CompletedProcess(command, 0, f"{folder}\r\n", "")

        with mock.patch.object(sim.subprocess, "run", run), mock.patch.object(sim.os, "kill",
                                                                              side_effect=ProcessLookupError), \
                redirect_stdout(io.StringIO()):
            capture.ios([self.phone])
        self.assertIn(["delete", "STALE"], calls, "a killed run's clone")
        self.assertLess(calls.index(["shutdown", "PHONE"]), calls.index(["clone", "PHONE", screens.sim.clone_name(
            "fixture screens phone", 1)]), "the kept simulator clones only when shut down")
        self.assertEqual(sorted(language for _, language in launches), ["de", "en", "it"])
        self.assertEqual({udid for udid, _ in launches}, {"CLONE1", "CLONE2"}, "two clones side by side")
        self.assertEqual(sorted(c for c in calls if c[0] == "boot"), [["boot", "CLONE1"], ["boot", "CLONE2"]],
                         "one boot each: the defaults come with the clone")
        self.assertEqual(sorted(c[1] for c in calls if c[0] == "delete"), ["CLONE1", "CLONE2", "STALE"])
        for language in ("en", "it", "de"):
            self.assertTrue((self.screens.out / "phone" / language / "home-light.png").is_file())
        self.assertEqual(capture.problems, [])
        self.assertTrue((self.screens.out / "logs" / "phone-1.log").is_file(), "a log per clone")

    def test_android_boots_one_emulator_per_platform_and_runs_each_language_on_it(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["android"] = {"dir": "android"}
        data["screens"]["platforms"]["droid"] = {"capture": "android", "avd": "Pixel_8", "posture": "open"}
        data["screens"]["list"]["home"]["android"] = "today"
        data["screens"]["list"]["settings"]["droid"] = {
            "shot": "settings", "env": {"LACHING_SCREENSHOT_MODE": "two"}, "camera": True}
        path.write_text(json.dumps(data), "utf8")
        manifest_ = manifest(self.root)
        droid = manifest_.platforms[-1]
        self.assertEqual(manifest_.pick_platforms(["android"]), [droid])
        self.assertFalse(droid.landscape)
        self.assertEqual(manifest_.shot("settings", droid),
                         screens.Shot("settings", (("LACHING_SCREENSHOT_MODE", "two"),), True))
        apk = self.root / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        apk.parent.mkdir(parents=True)
        apk.write_bytes(b"apk")
        calls = []

        class FakeEmulator:
            def __init__(self, avd, index, log, camera=False, cold=False):
                calls.append(("boot", avd, index, camera, cold, log.name))

            def boot(self):
                pass

            def install(self, apk):
                calls.append(("install", apk.name))

            def launcher(self, app_id):
                return f"{app_id}/.Main"

            def posture(self, name):
                calls.append(("posture", name))

            def orientation(self, landscape):
                calls.append(("orientation", landscape))

            def run(self, app_id, component, language, region, shots, extras, raw):
                calls.append(("run", component, language, region, shots, extras))
                raw.mkdir(parents=True)
                for shot in shots:
                    for theme in ("light", "dark"):
                        (raw / f"{shot}-{theme}.png").write_bytes(b"png")
                if extras and language == "de":
                    (raw / "problems.txt").write_text("settings-light.png: a label ends in …\n", "utf8")

            def kill(self):
                calls.append(("kill",))

        capture = screens.Capture(manifest_, [droid], ["en", "de"], ["home", "settings"], ["light"], 1, False, True)
        with mock.patch.object(screens.avd, "Emulator", FakeEmulator), redirect_stdout(io.StringIO()):
            code = capture.run()
        self.assertEqual(code, 1)
        self.assertEqual(calls, [
            ("boot", "Pixel_8", 0, True, True, "droid.log"), ("install", "app-debug.apk"), ("posture", "open"),
            ("run", "app.fixture.dev/.Main", "en", "US", ["today"], {}),
            ("run", "app.fixture.dev/.Main", "en", "US", ["settings"], {"LACHING_SCREENSHOT_MODE": "two"}),
            ("run", "app.fixture.dev/.Main", "de", "DE", ["today"], {}),
            ("run", "app.fixture.dev/.Main", "de", "DE", ["settings"], {"LACHING_SCREENSHOT_MODE": "two"}),
            ("kill",)])
        self.assertEqual(sorted(p.name for p in (manifest_.out / "droid" / "en").iterdir()),
                         ["home-light.png", "settings-light.png"])
        self.assertEqual(capture.problems, [(3, "de", "settings-light.png: a label ends in …")])
        self.assertEqual(capture.ran, {"droid"})

    def test_android_without_a_build_or_an_avd_reports_it_for_every_language(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["android"] = {"dir": "android"}
        data["screens"]["platforms"]["droid"] = {"capture": "android"}
        data["screens"]["list"]["home"]["android"] = "today"
        path.write_text(json.dumps(data), "utf8")
        manifest_ = manifest(self.root)
        droid = manifest_.platforms[-1]
        capture = screens.Capture(manifest_, [droid], ["en", "de"], ["home"], ["light"], 1, False)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(capture.run(), screens.VM_UNAVAILABLE)
        self.assertEqual([text for _, _, text in capture.problems],
                         [f"no Debug APK in {self.root / 'android' / 'app' / 'build' / 'outputs' / 'apk' / 'debug'} "
                          "(./gradlew :app:assembleDebug)"] * 2)
        (self.root / "android" / "app" / "build" / "outputs" / "apk" / "debug").mkdir(parents=True)
        (self.root / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk").write_bytes(b"apk")
        capture = screens.Capture(manifest_, [droid], ["en"], ["home"], ["light"], 1, False)
        with redirect_stdout(io.StringIO()):
            capture.run()
        self.assertEqual([text for _, _, text in capture.problems],
                         ["no avd in screens.platforms (an AVD name, avdmanager list avd)"])

    def test_a_platform_that_didnt_run_reports_its_problems_in_one_list(self):
        capture = screens.Capture(self.screens, [self.mac], ["en"], ["panel", "home"], ["light"], 1, False)
        raw = self.root / "raw"
        raw.mkdir()
        (raw / "problems.txt").write_text("home: the text doesn't fit\n", "utf8")
        log = self.root / "run.log"
        log.write_text("Problems (en):\n  ignored, problems.txt wins\n", "utf8")
        capture.problems_from(self.mac, "en", raw, 1, log)
        (raw / "problems.txt").unlink()
        capture.problems_from(self.mac, "it", raw, 1, log)
        log.write_text("crashed\n", "utf8")
        capture.problems_from(self.mac, "de", raw, 2, log)
        capture.collect(self.mac, "en", raw)
        with redirect_stdout(io.StringIO()):
            capture.write_problems(60)
        report = (self.screens.out / "problems.txt").read_text("utf8").splitlines()
        self.assertTrue(report[0].startswith("# screens.py capture: mac in en, 2 screens, light"))
        self.assertEqual(report[1:], [
            "mac de: the run failed (exit 2); see " + str(log),
            "mac en: home: the text doesn't fit",
            "mac en: no panel-light.png for panel",
            "mac en: no home-light.png for home",
            "mac it: ignored, problems.txt wins",
        ])
        # A later run of another platform keeps these; one of the same platform and language replaces them.
        windows = screens.Capture(self.screens, [self.windows, self.mac], ["it"], ["panel"], ["light"], 1, False)
        windows.problem(self.windows, "it", "panel: cut off")
        with redirect_stdout(io.StringIO()):
            windows.write_problems(60)
        report = (self.screens.out / "problems.txt").read_text("utf8").splitlines()
        self.assertEqual([line for line in report if not line.startswith("#")], [
            "mac de: the run failed (exit 2); see " + str(log),
            "mac en: home: the text doesn't fit",
            "mac en: no panel-light.png for panel",
            "mac en: no home-light.png for home",
            "windows it: panel: cut off",
        ])

    def test_a_run_without_a_shot_keeps_the_last_one(self):
        folder = self.screens.folder(self.mac, "en")
        folder.mkdir(parents=True)
        (folder / "home-light.png").write_bytes(b"last")
        raw = self.root / "raw"
        raw.mkdir()
        capture = screens.Capture(self.screens, [self.mac], ["en"], ["home"], ["light", "dark"], 1, False)
        self.assertEqual(capture.collect(self.mac, "en", raw), 0)
        self.assertEqual((folder / "home-light.png").read_bytes(), b"last", "a failed run leaves the last shot")
        self.assertEqual([text for _, _, text in capture.problems],
                         ["no home-light.png for home; kept the last one", "no home-dark.png for home"])

    def test_the_jobs_are_shared_among_the_platforms_and_the_themes_reach_every_run(self):
        capture = screens.Capture(self.screens, [self.mac, self.windows, self.phone], ["en", "it", "de"], ["home"],
                                  ["dark"], 6, False)
        groups = {"mac": [self.mac], "windows": [self.windows], "ios": [self.phone], "android": []}
        self.assertEqual(capture.shares(groups), {"mac": 2, "windows": 2, "ios": 2}, "six in all, two each")
        with mock.patch.object(capture, "mac") as mac, mock.patch.object(capture, "windows") as windows, \
                mock.patch.object(capture, "ios") as ios, redirect_stdout(io.StringIO()):
            capture.run()
        mac.assert_called_once_with(self.mac, 2)
        windows.assert_called_once_with(self.windows, 2)
        ios.assert_called_once_with([self.phone], 2)
        commands = []
        with mock.patch.object(capture, "sh", side_effect=lambda command, *_, **__: commands.append(command) or 0), \
                redirect_stdout(io.StringIO()):
            capture.mac(self.mac, 1)
            capture.windows(self.windows, 2)
        mac_runs = [c for c in commands if str(c[0]).endswith("screenshots.sh")]
        self.assertEqual([c[1:6] for c in mac_runs], [["--only", "home", "--hidden", "--themes", "dark"]] * 3)
        self.assertEqual([c[-1] for c in mac_runs], ["en", "it", "de"])
        self.assertEqual([c[1:] for c in commands if str(c[0]).endswith("vm.sh")],
                         [["check-layout", "en", "it", "de", "--", "-Only", "home", "-Themes", "dark", "-Jobs", "2"]])

    def test_usage_mistakes_exit_2(self):
        for args, words in ((["capture", "--platforms", "linux"], "unknown platform"),
                            (["compare", "--themes", "sepia"], "--themes"),
                            (["accept", "../x"], "baseline name")):
            with self.subTest(args=args):
                error = io.StringIO()
                with redirect_stderr(error), redirect_stdout(io.StringIO()):
                    self.assertEqual(screens.main(args), 2)
                self.assertIn(words, error.getvalue())


class SimulatorTests(FixtureAppTestCase):
    def test_a_run_moves_the_folder_the_app_drew_into_and_says_when_it_drew_nothing(self):
        drawn = self.root / "container"
        drawn.mkdir()
        (drawn / "home-light.png").write_bytes(b"png")
        out = self.root / "out"
        out.mkdir()
        (out / "old-light.png").write_bytes(b"old")
        launches = []

        def run(command, env=None, **_):
            launches.append((command[-4:], env["SIMCTL_CHILD_LACHING_SCREENSHOT_MODE"]))
            return subprocess.CompletedProcess(command, 0, f"app.fixture: 42\r\n{drawn}\r\n" if drawn.exists() else
                                               "app.fixture: 42\r\nCrashed\r\n", "")

        log = self.root / "phone.log"
        phone = sim.Simulator("UDID", log)
        with mock.patch.object(sim.subprocess, "run", run):
            self.assertEqual(phone.run("app.fixture", "pt-BR", {"LACHING_SCREENSHOT_MODE": "home"}, out), out)
            with self.assertRaisesRegex(RuntimeError, r"no shots were drawn \(exit 0\): app.fixture: 42\nCrashed"):
                phone.run("app.fixture", "it", {"LACHING_SCREENSHOT_MODE": "home"}, out)
        self.assertEqual(launches[0], (["-AppleLanguages", "(pt-BR)", "-AppleLocale", "pt_BR"], "home"))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["home-light.png"], "the folder replaces the old shots")
        self.assertFalse(drawn.exists(), "moved out of the app's container")
        self.assertIn("SIMCTL_CHILD_LACHING_SCREENSHOT_MODE=home xcrun simctl launch", log.read_text("utf8"))

    def test_a_run_that_hangs_is_terminated(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[2] == "launch":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(sim.subprocess, "run", run), self.assertRaisesRegex(RuntimeError, "didn't finish in 5 s"):
            sim.Simulator("UDID").run("app.fixture", "en", {}, self.root / "out", timeout=5)
        self.assertEqual(calls[-1], ["xcrun", "simctl", "terminate", "UDID", "app.fixture"])

    def test_the_newest_runtime_that_runs_a_device_type(self):
        runtimes = [{"version": "26.4", "supportedDeviceTypes": [{"identifier": "t.SE", "name": "iPhone SE"}]},
                    {"version": "26.10", "supportedDeviceTypes": [{"identifier": "t.SE", "name": "iPhone SE"}]},
                    {"version": "27.0", "supportedDeviceTypes": [{"identifier": "t.Duo", "name": "iPhone Duo"}]}]
        self.assertEqual(sim.newest_runtime("t.SE", runtimes)["version"], "26.10", "by number, not by text")
        self.assertEqual(sim.newest_runtime("iPhone Duo", runtimes)["version"], "27.0", "by name too")
        self.assertIsNone(sim.newest_runtime("iPad", runtimes))

    def test_the_simulator_budget_is_shared_in_proportion_to_the_runs(self):
        self.assertEqual(sim.shares(4, {"se": 10, "ipad": 30}), {"se": 1, "ipad": 3})
        self.assertEqual(sim.shares(1, {"se": 10, "ipad": 30}), {"se": 1, "ipad": 1}, "at least one each")
        self.assertEqual(sim.shares(8, {"phone": 2}), {"phone": 2}, "never more than its runs")
        self.assertEqual(sim.shares(3, {"a": 5, "b": 5, "c": 5}), {"a": 1, "b": 1, "c": 1})

    def clones(self, starts):
        """on_clones over one group of two clones, `starts(number)` raising for a clone that doesn't start."""
        calls, done, failed, notes = [], [], [], []
        lock = sim.threading.Lock()

        def run(command, **_):
            with lock:
                calls.append(command[2:])
            made = f"CLONE{command[-1].split('#')[1][0]}\n" if command[2] == "clone" else ""
            return subprocess.CompletedProcess(command, 0, made, "")

        def prepare(clone):
            starts(int(clone.udid[-1]))

        def work(clone, task):
            with lock:
                done.append((clone.udid, task))

        group = sim.Clones("TEMPLATE", "app screens phone", ["en", "it", "de"], 2, self.root / "phone.log")
        with mock.patch.object(sim.subprocess, "run", run):
            sim.on_clones([group], prepare, work, lambda task, error: failed.append((task, str(error))), notes.append)
        return calls, done, failed, notes

    def test_clones_share_a_groups_tasks_and_are_deleted(self):
        calls, done, failed, notes = self.clones(lambda number: None)
        self.assertEqual(sorted(task for _, task in done), ["de", "en", "it"])
        self.assertEqual(sorted(c for c in calls if c[0] == "clone"),
                         [["clone", "TEMPLATE", sim.clone_name("app screens phone", n)] for n in (1, 2)])
        self.assertEqual(sorted(c for c in calls if c[0] == "delete"), [["delete", "CLONE1"], ["delete", "CLONE2"]])
        self.assertTrue((self.root / "phone-2.log").is_file(), "each clone logs on its own")
        self.assertEqual((failed, notes), ([], []))

    def test_a_clone_that_doesnt_start_leaves_its_tasks_to_the_others(self):
        def starts(number):
            if number == 2:
                raise RuntimeError("no boot")

        calls, done, failed, notes = self.clones(starts)
        self.assertEqual(sorted(done), [("CLONE1", "de"), ("CLONE1", "en"), ("CLONE1", "it")])
        self.assertEqual(failed, [])
        self.assertEqual(notes, [f"{sim.clone_name('app screens phone', 2)} didn't start (no boot)"])
        self.assertIn(["delete", "CLONE2"], calls, "deleted all the same")

    def test_when_no_clone_starts_every_task_fails(self):
        def starts(number):
            raise RuntimeError("no boot")

        _, done, failed, notes = self.clones(starts)
        self.assertEqual(done, [])
        self.assertEqual(sorted(failed), [("de", "no boot"), ("en", "no boot"), ("it", "no boot")])
        self.assertEqual(len(notes), 2)

    def test_the_clones_of_a_run_that_was_killed_are_deleted(self):
        listed = [{"name": "app screens phone clone #1 (1)", "udid": "LIVE"},
                  {"name": "app screens phone clone #2 (999999999)", "udid": "DEAD"},
                  {"name": "app layout clone iPhone-SE #1 (999999999)", "udid": "OLD"},
                  {"name": "other screens phone clone #1 (999999999)", "udid": "OTHER"},
                  {"name": "app screens phone", "udid": "KEPT"}]
        deleted = []

        def kill(pid, _):
            if pid != 1:
                raise ProcessLookupError

        with mock.patch.object(sim, "devices", return_value=listed), mock.patch.object(sim.os, "kill", kill), \
                mock.patch.object(sim.Simulator, "delete", lambda self: deleted.append(self.udid)):
            sim.clean_stale_clones("app")
        self.assertEqual(deleted, ["DEAD", "OLD"], "the old naming too; never another app's, a live run's, a kept one")

    def test_the_emulator_logs_each_command_with_its_seconds(self):
        with mock.patch.object(avd, "sdk_tool", return_value="adb"), mock.patch.object(
                avd.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", "")):
            avd.Emulator("Pixel", 0, self.root / "droid.log").shell("getprop")
        self.assertRegex((self.root / "droid.log").read_text("utf8"),
                         r"^\$ adb -s emulator-5554 shell getprop\nok\n\(\d+\.\d s\)\n$")


class SdkToolTests(unittest.TestCase):
    def test_off_path_it_takes_the_sdk_tool_from_android_home_else_android_studios_folder(self):
        name = "adb.exe" if avd.os.name == "nt" else "adb"  # the SDK's own file on this OS
        with tempfile.TemporaryDirectory() as temp:
            home, local = Path(temp) / "home", Path(temp) / "local"
            for sdk in (Path(temp) / "sdk", local / "Android" / "Sdk"):
                (sdk / "platform-tools").mkdir(parents=True)
                (sdk / "platform-tools" / name).write_bytes(b"")
            environ = {key: value for key, value in avd.os.environ.items()
                       if key not in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "LOCALAPPDATA")}
            with mock.patch.object(avd.shutil, "which", return_value=None),                     mock.patch.object(avd.Path, "home", return_value=home),                     mock.patch.dict(avd.os.environ, environ, clear=True):
                with mock.patch.dict(avd.os.environ, {"ANDROID_HOME": str(Path(temp) / "sdk")}):
                    self.assertEqual(avd.sdk_tool("adb"), str(Path(temp) / "sdk" / "platform-tools" / name))
                with mock.patch.dict(avd.os.environ, {"LOCALAPPDATA": str(local)}):
                    self.assertEqual(avd.sdk_tool("adb"), str(local / "Android" / "Sdk" / "platform-tools" / name))
                with self.assertRaisesRegex(RuntimeError, "no adb on PATH or under ANDROID_HOME"):
                    avd.sdk_tool("adb")


class SnapshotTests(unittest.TestCase):
    """avd.Emulator.boot's choice between the AVD's snapshot and a cold boot, against a faked device: `snapshots` is
    what each saved snapshot's RAM holds, `broken` the ones the emulator can't load (it cold-boots instead), `exits`
    the ones it dies on."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.sdk = root / "sdk"
        self.image = self.sdk / "system-images" / "android-36" / "google_apis" / "arm64-v8a"
        self.image.mkdir(parents=True)
        (self.image / "source.properties").write_text("Pkg.Revision=7\n", "utf8")
        (self.sdk / "emulator").mkdir()
        (self.sdk / "emulator" / "emulator").write_bytes(b"")
        (self.sdk / "emulator" / "source.properties").write_text("Pkg.Desc=Android Emulator\nPkg.Revision=37.2.12\n",
                                                                 "utf8")
        self.folder = root / "elsewhere" / "Pixel_8.avd"  # where the AVD's .ini says it is
        self.folder.mkdir(parents=True)
        (self.folder / "config.ini").write_text("hw.ramSize=2G\nimage.sysdir.1=system-images/android-36/google_apis/"
                                                "arm64-v8a/\n", "utf8")
        (root / "avd").mkdir()
        (root / "avd" / "Pixel_8.ini").write_text(f"avd.ini.encoding=UTF-8\npath={self.folder}\n", "utf8")
        self.log = root / "emulator.log"
        self.log.write_text("", "utf8")
        tools = {"adb": str(self.sdk / "platform-tools" / "adb"), "emulator": str(self.sdk / "emulator" / "emulator")}
        for patch in (mock.patch.dict(avd.os.environ, {"ANDROID_AVD_HOME": str(root / "avd")}),
                      mock.patch.object(avd, "sdk_tool", side_effect=tools.__getitem__)):
            patch.start()
            self.addCleanup(patch.stop)
        self.snapshots: dict[str, dict] = {}
        self.broken: set[str] = set()
        self.exits: set[str] = set()
        self.saves_fail = False
        self.clock_behind = False

    def boot(self, camera: bool = False, cold: bool = False) -> list:
        """Boots a faked emulator; what it did, in order."""
        test, calls = self, []

        class Fake(avd.Emulator):
            ram: dict = {}

            def start(self, snapshot):
                calls.append(("start", *snapshot))
                self.ram = {}
                if "-snapshot" in snapshot:
                    name = snapshot[snapshot.index("-snapshot") + 1]
                    if name in test.exits:
                        raise RuntimeError("the emulator exited (1)")
                    if name not in test.broken:
                        self.ram = dict(test.snapshots.get(name, {}))

            def shell(self, *args, timeout=120, quiet=False):
                if args[0] == "setprop":
                    self.ram[args[1]] = args[2]
                if args[0] == "date":  # a loaded snapshot's clock is behind until Android takes the host's
                    calls.append(("date",))
                    behind = 3600 if test.clock_behind and calls.count(("date",)) == 1 else 0
                    return str(int(avd.time.time()) - behind)
                return self.ram.get(args[1], "") if args[0] == "getprop" else ""

            def adb(self, *args, timeout=120, binary=False, quiet=False):
                calls.append(args)
                if args[:4] == ("emu", "avd", "snapshot", "save") and not test.saves_fail:
                    test.snapshots[args[4]] = dict(self.ram)
                    return subprocess.CompletedProcess(args, 0, "OK\n", "")
                return subprocess.CompletedProcess(args, 0, "KO: no\n" if test.saves_fail else "", "")

            def demo_mode(self):
                calls.append(("demo",))

            def kill(self):
                calls.append(("kill",))

        Fake("Pixel_8", 1, self.log, camera=camera, cold=cold).boot()
        return calls

    COLD = [("start", "-no-snapshot-load", "-no-snapshot-save"), ("demo",),
            ("emu", "avd", "snapshot", "save", "laching-screens")]
    QUICK = [("start", "-snapshot", "laching-screens", "-no-snapshot-save"), ("date",), ("demo",)]

    def marker(self, name: str = "laching-screens") -> dict:
        return json.loads((self.folder / f"{name}.json").read_text("utf8"))

    def test_the_first_run_cold_boots_and_saves_the_snapshot_and_later_runs_load_it(self):
        self.assertEqual(self.boot(), self.COLD)
        marker = self.marker()
        self.assertEqual(marker["fingerprint"], {
            "setup": avd.SETUP, "emulator": "37.2.12", "image": "system-images/android-36/google_apis/arm64-v8a/",
            "imageRevision": "7"})
        self.assertEqual(self.snapshots["laching-screens"], {avd.TOKEN: marker["token"]}, "the token is in the save")
        self.assertEqual(self.boot(), self.QUICK, "demo mode again, but no save: a run never changes the snapshot")
        self.assertEqual(self.boot(), self.QUICK)
        self.assertIn("(quick boot from laching-screens)", self.log.read_text("utf8"))

    def test_a_quick_boot_waits_for_the_clock_to_catch_up_with_the_hosts(self):
        self.boot()
        self.clock_behind = True
        with mock.patch.object(avd.time, "sleep") as sleep:
            self.assertEqual(self.boot(), [self.QUICK[0], ("date",), ("date",), ("demo",)])
        sleep.assert_called_once_with(1)

    def test_cold_boots_and_saves_anew_even_with_a_snapshot_to_load(self):
        self.boot()
        token = self.marker()["token"]
        self.assertEqual(self.boot(cold=True), self.COLD)
        self.assertNotEqual(self.marker()["token"], token)
        self.assertEqual(self.boot(), self.QUICK)

    def test_a_new_emulator_or_system_image_makes_a_new_snapshot(self):
        self.boot()
        (self.sdk / "emulator" / "source.properties").write_text("Pkg.Revision=37.3.1\n", "utf8")
        self.assertEqual(self.boot(), self.COLD)
        self.assertEqual(self.marker()["fingerprint"]["emulator"], "37.3.1")
        (self.image / "source.properties").write_text("Pkg.Revision=8\n", "utf8")
        self.assertEqual(self.boot(), self.COLD)
        self.assertEqual(self.boot(), self.QUICK)

    def test_without_source_properties_the_files_size_and_time_stand_in(self):
        (self.image / "source.properties").unlink()
        (self.image / "system.img").write_bytes(b"image")
        self.boot()
        self.assertRegex(self.marker()["fingerprint"]["imageRevision"], r"^5@\d+$")
        self.assertEqual(self.boot(), self.QUICK)
        (self.image / "system.img").write_bytes(b"a new image")
        self.assertEqual(self.boot(), self.COLD)

    def test_a_snapshot_the_emulator_couldnt_load_is_saved_again_from_the_cold_boot_it_did_instead(self):
        self.boot()
        token = self.marker()["token"]
        self.broken.add("laching-screens")
        self.assertEqual(self.boot(), [("start", "-snapshot", "laching-screens", "-no-snapshot-save"), ("demo",),
                                       ("emu", "avd", "snapshot", "save", "laching-screens")])
        self.assertNotEqual(self.marker()["token"], token)
        self.assertIn("didn't load", self.log.read_text("utf8"))

    def test_an_emulator_that_dies_loading_the_snapshot_boots_again_cold(self):
        self.boot()
        self.exits.add("laching-screens")
        self.assertEqual(self.boot(), [("start", "-snapshot", "laching-screens", "-no-snapshot-save"), ("kill",),
                                       *self.COLD])

    def test_a_failed_save_leaves_no_marker_so_the_next_run_cold_boots_too(self):
        self.boot()
        self.saves_fail = True
        self.boot(cold=True)
        self.assertFalse((self.folder / "laching-screens.json").exists())
        self.saves_fail = False
        self.assertEqual(self.boot(), self.COLD)

    def test_a_marker_that_isnt_json_is_no_marker(self):
        (self.folder / "laching-screens.json").write_text("{", "utf8")
        self.assertEqual(self.boot(), self.COLD)

    def test_a_run_with_the_webcam_has_a_snapshot_of_its_own(self):
        self.boot()
        calls = self.boot(camera=True)
        self.assertEqual(calls, [("start", "-no-snapshot-load", "-no-snapshot-save"), ("demo",),
                                 ("emu", "avd", "snapshot", "save", "laching-screens-camera")])
        self.assertEqual(self.boot(camera=True)[0], ("start", "-snapshot", "laching-screens-camera", "-no-snapshot-save"))
        self.assertEqual(self.boot(), self.QUICK, "the plain one is still there")

    def test_the_avd_folder_is_the_ini_files_path_else_name_avd_beside_it(self):
        self.assertEqual(avd.avd_folder("Pixel_8"), self.folder)
        self.assertEqual(avd.avd_folder("Other"), Path(avd.os.environ["ANDROID_AVD_HOME"]) / "Other.avd")
        environ = {key: value for key, value in avd.os.environ.items() if not key.startswith("ANDROID_")}
        with mock.patch.dict(avd.os.environ, environ, clear=True), \
                mock.patch.object(avd.Path, "home", return_value=Path("/home/me")):
            self.assertEqual(avd.avd_folder("Other"), Path("/home/me/.android/avd/Other.avd"))
            with mock.patch.dict(avd.os.environ, {"ANDROID_USER_HOME": "/u"}):
                self.assertEqual(avd.avd_folder("Other"), Path("/u/avd/Other.avd"))

    def test_folding_shut_dismisses_the_lock_screen_the_cover_screen_shows(self):
        calls = []

        class Fake(avd.Emulator):
            locked = True

            def adb(self, *args, timeout=120, binary=False, quiet=False):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, "OK\n", "")

            def shell(self, *args, timeout=120, quiet=False):
                calls.append(args)
                if args[:2] == ("wm", "dismiss-keyguard"):
                    self.locked = False
                if args[:2] == ("cmd", "device_state"):
                    return "Committed state: DeviceState{identifier=0, name='CLOSED', app_accessible=true}"
                return f"  mHasSurface=true isKeyguardShowing={str(self.locked).lower()}" if args[0] == "dumpsys" else ""

        with mock.patch.object(avd.time, "sleep"):
            Fake("Pixel_8", 1, self.log).posture("closed")
        self.assertEqual(calls, [("emu", "posture", "1"), ("cmd", "device_state", "state"),
                                 ("dumpsys", "window", "displays"), ("wm", "dismiss-keyguard"),
                                 ("dumpsys", "window", "displays")])


class LayoutReportTests(unittest.TestCase):
    def report(self, results):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = layoutcheck.report(results, "ios", ["se", "ipad"], ["en", "de"], Path("/shots"), 0.0)
        return code, out.getvalue(), err.getvalue()

    def test_names_each_device_and_language_and_what_failed(self):
        code, out, err = self.report({("se", "en"): [], ("se", "de"): ["home-light.png: cut short"], ("ipad", "en"): []})
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), ["== ios-se en", "== ios-se de", "  home-light.png: cut short", "== ios-ipad en",
                                            "== ios-ipad de", "  not run"])
        shots = re.escape(str(Path("/shots")))  # the path as this OS writes it
        self.assertRegex(err, rf"^Layout problems in: ios-se/de ios-ipad/de \(shots in {shots}, [\d.]+ min\)$")

    def test_says_when_every_screen_fits(self):
        code, out, err = self.report({(d, lang): [] for d in ("se", "ipad") for lang in ("en", "de")})
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out.splitlines()[-1], r"^Every screen fits on se, ipad in: en de \([\d.]+ min\)$")


@unittest.skipIf(Image is None, NO_PILLOW)
class ImageTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.screens = manifest(self.root)
        self.mac, self.windows, self.phone = self.screens.platforms

    def shot(self, path: Path, size: tuple[int, int], color, mark=None) -> Path:
        image = Image.new("RGB", size, color)
        if mark:
            ImageDraw.Draw(image).rectangle(mark, fill=(250, 250, 250))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return path

    def test_windows_shots_are_cropped_to_the_window(self):
        image = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((20, 10, 219, 159), fill=(200, 30, 30, 255))
        self.assertEqual(screens.crop_to_window(image).size, (200, 150))
        flat = Image.new("RGB", (300, 200), (12, 12, 12))
        ImageDraw.Draw(flat).rectangle((50, 40, 149, 139), fill=(240, 240, 240))
        self.assertEqual(screens.crop_to_window(flat).size, (100, 100))
        whole = Image.new("RGB", (30, 20), (1, 2, 3))
        self.assertIs(screens.crop_to_window(whole), whole, "nothing to crop")

    def test_collect_names_the_shots_canonically(self):
        raw = self.root / "raw"
        self.shot(raw / "today-dark.png", (40, 80), (10, 10, 10))
        capture = screens.Capture(self.screens, [self.phone], ["en"], ["home"], ["dark"], 1, False)
        self.assertEqual(capture.collect(self.phone, "en", raw), 1)
        self.assertTrue((self.screens.out / "phone" / "en" / "home-dark.png").is_file())
        self.assertEqual(capture.problems, [])

    def test_a_sheet_puts_the_desktops_above_the_phones(self):
        out = self.screens.out
        self.shot(out / "mac" / "en" / "home-light.png", (400, 300), (200, 200, 255))
        self.shot(out / "windows" / "en" / "home-light.png", (200, 100), (255, 200, 200))
        self.shot(out / "phone" / "en" / "home-light.png", (100, 200), (200, 255, 200))
        sheet = screens.sheet(self.screens, "home", "en", "light")
        self.assertGreater(sheet.height, screens.TOP_HEIGHT + screens.BOTTOM_HEIGHT)
        # A row is one height: the Mac's 4:3 and Windows' 2:1 side by side at TOP_HEIGHT.
        self.assertGreaterEqual(sheet.width, round(screens.TOP_HEIGHT * 4 / 3) + screens.TOP_HEIGHT * 2)
        self.assertIsNone(screens.sheet(self.screens, "home", "it", "light"), "nothing captured in Italian")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(screens.compare(self.screens, ["en"], ["panel", "home"], ["light"]), 0)
        self.assertTrue((out / "sheets" / "en" / "home-light.jpg").is_file())
        self.assertFalse((out / "sheets" / "en" / "panel-light.jpg").exists(), "no capture of panel")
        page = (out / "index.html").read_text("utf8")
        self.assertIn('"sheets/en/home-light.jpg"', page)

    def test_diff_shows_what_changed_since_the_accepted_run(self):
        out = self.screens.out
        home = self.shot(out / "mac" / "en" / "home-light.png", (160, 120), (30, 30, 30), (10, 10, 60, 40))
        panel = self.shot(out / "mac" / "en" / "panel-light.png", (160, 120), (30, 30, 30), (90, 60, 150, 110))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(screens.accept(self.screens, "accepted"), 0)
            self.shot(home, (160, 120), (30, 30, 30), (10, 10, 61, 40))  # a pixel's difference: the same
            self.shot(panel, (160, 120), (30, 30, 30), (10, 60, 70, 110))  # moved: changed
            self.shot(out / "phone" / "en" / "home-light.png", (60, 120), (0, 0, 0))  # new
            self.assertEqual(screens.diff(self.screens, "accepted", 4), 0)
        changes = json.loads((out / "changes.json").read_text("utf8"))
        self.assertEqual(changes["changed"], ["mac/en/panel-light.png"])
        self.assertEqual(changes["new"], ["phone/en/home-light.png"])
        self.assertEqual(screens.changed_sheets(changes), {("en", "panel"), ("en", "home")})
        shutil.rmtree(out / "phone")
        panel.unlink()
        with redirect_stdout(io.StringIO()):
            screens.diff(self.screens, "accepted", 4)
        self.assertEqual(json.loads((out / "changes.json").read_text("utf8"))["gone"], ["mac/en/panel-light.png"])

    def test_the_hash_ignores_scale_but_not_layout(self):
        a = Image.new("RGB", (320, 240), (0, 0, 0))
        ImageDraw.Draw(a).rectangle((20, 20, 120, 100), fill=(255, 255, 255))
        self.assertLessEqual(screens.distance(screens.dhash(a), screens.dhash(a.resize((160, 120)))), 4)
        b = Image.new("RGB", (320, 240), (0, 0, 0))
        ImageDraw.Draw(b).rectangle((180, 120, 300, 220), fill=(255, 255, 255))
        self.assertGreater(screens.distance(screens.dhash(a), screens.dhash(b)), 20)


if __name__ == "__main__":
    unittest.main()
