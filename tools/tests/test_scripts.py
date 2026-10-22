#!/usr/bin/env python3
"""Tests for the shell scripts and the small tools of tools/: conformance.sh, screenshots.sh and install-dev.sh run
against fake `swift` / `xcodebuild` commands over the fixture app; fonts.py's plan comes from companion.json;
store-screenshots.py and dev-icon.swift run when Pillow and a Mac with Swift are around."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import companion  # noqa: E402
import fonts  # noqa: E402
from support import (  # noqa: E402
    BASH, HERE, NO_SHELL, TOOLS, WINDOWS, FixtureAppTestCase, fixture_app, git, search_path, shell,
)


def png(path: Path, size: int, rgba=(255, 200, 0, 255)) -> None:
    raw = b"".join(b"\x00" + bytes(rgba) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class ScriptTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        self.log = self.root / "calls.log"

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        # LF on Windows too: sh reads a \r as part of its line.
        path.write_text("#!/bin/sh\n" + body, "utf8", newline="\n")
        path.chmod(0o755)

    def fake_python(self, name: str, script: Path) -> None:
        """A fake command that runs the Python `script`: a shell script on macOS and Linux, a `.cmd` on Windows, which
        can't start a `#!/bin/sh` file."""
        if WINDOWS:
            (self.bin / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\n', "utf8")
        else:
            self.fake(name, f'exec "{sys.executable}" "{script}" "$@"\n')

    def run_script(self, script: str, *args: str, env: dict | None = None, cwd: Path | None = None):
        if script.endswith(".sh") and BASH is None:
            self.skipTest(NO_SHELL)
        environment = {**os.environ, "PATH": search_path(self.bin), **(env or {})}
        return subprocess.run(
            [*shell(TOOLS / script), *args] if script.endswith(".sh") else [sys.executable, str(TOOLS / script), *args],
            cwd=cwd or self.root,
            env=environment, capture_output=True, encoding="utf8",
        )

    def test_every_shell_script_parses(self):
        for script in TOOLS.glob("*.sh"):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, encoding="utf8")
            self.assertEqual(result.returncode, 0, f"{script.name}: {result.stderr}")
            self.assertTrue(os.access(script, os.X_OK), f"{script.name} isn't executable")

    def test_every_python_tool_prints_utf8(self):
        # A cp1252 Windows console can't encode the tools' arrows and ellipses: each sets UTF-8 before it prints.
        for script in TOOLS.rglob("*.py"):
            text = script.read_text("utf8")
            if "tests" in script.parts or '__name__ == "__main__"' not in text:
                continue
            main = text.split('__name__ == "__main__":', 1)[1]
            self.assertRegex("\n".join(main.split("\n")[1:3]), r"utf8_console\(\)|stream\.reconfigure\(",
                             f"{script.relative_to(TOOLS)}: call companion.utf8_console() first under __main__")

    def test_utf8_console_survives_a_cp1252_console(self):
        result = subprocess.run(
            [sys.executable, "-c", "import companion; companion.utf8_console(); print('a \\u2192 b \\u2026')"],
            cwd=TOOLS, env={**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode("utf8").strip(), "a → b …")

    def test_conformance_runs_the_apps_generator(self):
        self.fake("swift", f'echo "$FIXTURE_WRITE_CONFORMANCE $*" > "{self.log}"\n')
        result = self.run_script("conformance.sh", cwd=self.root / "macos")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.log.read_text().strip(),
            "1 test --package-path macos/FixtureCore --filter ConformanceTests/testWriteVectors",
        )
        self.assertIn("Updated conformance/ from macos/FixtureCore", result.stdout)

    def test_screenshots_build_run_and_copy(self):
        # xcodebuild "builds" an app that writes its PNGs, one more than asked for, and prints the folder.
        self.fake("xcodebuild", f'''echo "$*" > "{self.log}"
while [ $# -gt 0 ]; do [ "$1" = -derivedDataPath ] && derived="$2"; shift; done
app="$derived/Build/Products/Debug/Fixture.app/Contents/MacOS"
mkdir -p "$app"
cat > "$app/Fixture" <<'APP'
#!/bin/sh
out=$(mktemp -d)
echo "$LACHING_SCREENSHOT_MODE hidden=$LACHING_SCREENSHOT_HIDDEN skin=$LACHING_SCREENSHOT_SKIN themes=$LACHING_SCREENSHOT_THEMES $*" >> "{self.root / 'runs.log'}"
printf png > "$out/panel-light.png"
printf png > "$out/demo-light.png"
echo "$out"
APP
chmod +x "$app/Fixture"
''')
        out = self.root / "shots"
        result = self.run_script("screenshots.sh", "--only", "panel", str(out), "it")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-project Fixture.xcodeproj -scheme Fixture -configuration Debug", self.log.read_text())
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["panel-light.png"], "only the shots asked for")
        self.assertIn("1 screenshots in", result.stdout)
        # --only runs hidden unless --front; a full run comes forward; --skin wears one; --themes names the appearances;
        # --build-only doesn't run the app.
        self.run_script("screenshots.sh", "--only", "panel", "--front", str(out), "it")
        self.run_script("screenshots.sh", str(out), "it")
        self.run_script("screenshots.sh", "--skin", "auguste", str(out), "it")
        self.run_script("screenshots.sh", "--only", "panel", "--themes", "dark,light", str(out), "it")
        self.run_script("screenshots.sh", "--build-only")
        runs = (self.root / "runs.log").read_text().splitlines()
        self.assertEqual([line.split(" -Apple")[0] for line in runs],
                         ["panel hidden=1 skin= themes=", "panel hidden=0 skin= themes=", "all hidden=0 skin= themes=",
                          "all hidden=0 skin=auguste themes=", "panel hidden=1 skin= themes=dark,light"])

    def test_screenshots_build_with_the_shared_compilation_cache_unless_turned_off(self):
        self.fake("xcodebuild", f'echo "$*" > "{self.log}"\n')
        self.assertEqual(self.run_script("screenshots.sh", "--build-only").returncode, 0)
        arguments = self.log.read_text().split()
        self.assertIn("COMPILATION_CACHE_ENABLE_CACHING=YES", arguments)
        self.assertIn(f"COMPILATION_CACHE_CAS_PATH={companion.COMPILATION_CACHE}", arguments)
        self.assertIn("SWIFT_ENABLE_PROJECT_PREFIX_MAPPING=YES", arguments, "a build that's only run maps its paths")
        self.assertEqual(self.run_script("screenshots.sh", "--build-only",
                                         env={"LACHING_COMPILATION_CACHE": "0"}).returncode, 0)
        self.assertNotIn("COMPILATION_CACHE", self.log.read_text())

    def fake_app(self, problems: dict[str, str] | None = None) -> None:
        """xcodebuild "builds" an app that writes a shot and prints its folder; in the languages named in `problems`
        it also writes that problems.txt, as ScreenshotCapture.finish() does."""
        cases = "".join(f'  {lang}) printf "%s\\n" "{text}" > "$out/problems.txt" ;;\n' for lang, text in (problems or {}).items())
        self.fake("xcodebuild", f'''echo build >> "{self.log}"
while [ $# -gt 0 ]; do [ "$1" = -derivedDataPath ] && derived="$2"; shift; done
app="$derived/Build/Products/Debug/Fixture.app/Contents/MacOS"
mkdir -p "$app"
cat > "$app/Fixture" <<'APP'
#!/bin/sh
out=$(mktemp -d)
printf png > "$out/panel-light.png"
case "$4" in  # -AppleLanguages (xx) -AppleLocale xx
{cases}esac
echo "$out"
APP
chmod +x "$app/Fixture"
''')

    def test_screenshots_fail_on_the_problems_the_run_reports(self):
        self.fake_app({"it": "panel-light.png: the content needs 540 pt of height, the window has 520"})
        out = self.root / "shots"
        result = self.run_script("screenshots.sh", str(out), "it")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Problems (it):", result.stderr)
        self.assertIn("the content needs 540 pt", result.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["panel-light.png", "problems.txt"],
                         "the shots are still copied, the problems beside them")
        self.assertIn("the content needs 540 pt", (out / "problems.txt").read_text())
        self.assertEqual(self.run_script("screenshots.sh", str(out), "en").returncode, 0)
        self.assertFalse((out / "problems.txt").exists(), "a clean run removes the last run's problems")

    def test_check_layout_runs_every_language_and_names_the_ones_that_fail(self):
        self.fake_app({"de": "panel-light.png: the content needs 540 pt of height, the window has 520"})
        result = self.run_script("check-layout.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("==")], ["== en", "== it", "== de"])
        self.assertIn("Layout problems in: de", result.stderr)
        self.assertTrue((self.root / "macos" / "build" / "layout-check" / "it" / "panel-light.png").exists())
        self.assertEqual(self.log.read_text().split(), ["build"], "one build for every language")
        self.assertIn("panel-light.png: the content needs 540 pt", result.stderr)
        passing = self.run_script("check-layout.sh", "--only", "panel", "en", "it")
        self.assertEqual(passing.returncode, 0, passing.stderr)
        self.assertIn("Every window fits in: en it", passing.stdout)

    @unittest.skipIf(WINDOWS, "the fake xcrun is a shell script")
    def test_check_layout_ios_runs_the_languages_on_clones_and_names_what_fails(self):
        state = self.root / "simulators.json"
        fake_simctl = self.root / "fake_simctl.py"
        # The Simulator in a JSON file: one runtime with an SE and an iPad, made and cloned devices, and an app that
        # writes a shot (plus problems.txt in German) and prints its folder with the pty's \r, as the real one does.
        fake_simctl.write_text(f"""import fcntl, json, os, sys, tempfile
from pathlib import Path
state = Path({str(state)!r})
# The clones boot side by side: one simctl at a time reads and writes the state.
lock = open(str(state) + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
data = json.loads(state.read_text()) if state.exists() else {{"devices": [], "next": 1}}
args = sys.argv[2:]
with open({str(self.log)!r}, "a") as log:
    log.write(" ".join(args[:1] + [a for a in args[1:2] if a in ("-j",)]) + "\\n")
def save():
    state.write_text(json.dumps(data))
def new(name):
    udid = f"UDID-{{data['next']}}"
    data["next"] += 1
    data["devices"].append({{"udid": udid, "name": name, "state": "Shutdown", "isAvailable": True}})
    save()
    print(udid)
command = args[0]
if command == "list" and "runtimes" in args:
    types = [{{"identifier": "com.apple.CoreSimulator.SimDeviceType." + t}}
             for t in ("iPhone-SE-3rd-generation", "iPad-Pro-13-inch-M5-12GB")]
    print(json.dumps({{"runtimes": [{{"identifier": "iOS-27-0", "name": "iOS 27.0", "version": "27.0",
                                     "isAvailable": True, "platform": "iOS", "supportedDeviceTypes": types}}]}}))
elif command == "list":
    print(json.dumps({{"devices": {{"iOS-27-0": data["devices"]}}}}))
elif command == "create":
    new(args[1])
elif command == "clone":
    new(args[2])
elif command == "delete":
    data["devices"] = [d for d in data["devices"] if d["udid"] != args[1]]
    save()
elif command == "launch":
    out = Path(tempfile.mkdtemp())
    (out / "home-light.png").write_bytes(b"png")
    language = args[args.index("-AppleLanguages") + 1].strip("()")
    locale = args[args.index("-AppleLocale") + 1]
    with open({str(self.log)!r}, "a") as log:
        log.write(f"run {{language}} {{locale}} {{os.environ.get('SIMCTL_CHILD_LACHING_SCREENSHOT_MODE')}} "
                  f"{{os.environ.get('SIMCTL_CHILD_LACHING_SCREENSHOT_ORIENTATION', '-')}}\\n")
    if language == "de":
        (out / "problems.txt").write_text("home-light.png: “Wiederholungen” is cut short\\n")
    sys.stdout.write(f"app.fixture.dev: 4242\\r\\n{{out}}\\r\\n")
""", "utf8")
        self.fake("xcrun", f'exec "{sys.executable}" "{fake_simctl}" "$@"\n')
        app = self.root / "Fixture.app"
        app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "app.fixture.dev"}))

        result = self.run_script("check-layout-ios.py", "--app", str(app), "--devices", "se,ipad-landscape", "en", "de")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("==")],
                         ["== ios-se en", "== ios-se de", "== ios-ipad-landscape en", "== ios-ipad-landscape de"])
        self.assertIn("“Wiederholungen” is cut short", result.stdout)
        self.assertIn("Layout problems in: ios-se/de ios-ipad-landscape/de", result.stderr)
        checked = self.root / "macos" / "build" / "layout-check"
        self.assertTrue((checked / "ios-ipad-landscape" / "en" / "home-light.png").exists())
        self.assertTrue((checked / "ios-se" / "de" / "problems.txt").exists())
        runs = sorted(line for line in self.log.read_text().splitlines() if line.startswith("run "))
        self.assertEqual(runs, ["run de de_DE all -", "run de de_DE all landscape", "run en en_US all -",
                                "run en en_US all landscape"])
        names = [d["name"] for d in json.loads(state.read_text())["devices"]]
        self.assertEqual(sorted(names), ["fixture layout: iPad-Pro-13-inch-M5-12GB", "fixture layout: iPhone-SE-3rd-generation"],
                         "the templates stay, the clones go")

        self.log.unlink()
        passing = self.run_script("check-layout-ios.py", "--app", str(app), "--devices", "se", "--only", "home", "en")
        self.assertEqual(passing.returncode, 0, passing.stderr)
        self.assertIn("Every screen fits on se in: en", passing.stdout)
        self.assertNotIn("create", self.log.read_text().split(), "the template is made once")
        self.assertIn("run en en_US home -", self.log.read_text())

    def android_config(self, extra_shot: bool = True) -> None:
        """The fixture app with an Android side: `android/` and a `droid` platform captured on the AVD Pixel_8."""
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["android"] = {"dir": "android", "core": "core", "app": "app"}
        data["screens"]["platforms"]["droid"] = {"capture": "android", "label": "Android", "avd": "Pixel_8"}
        data["screens"]["list"]["home"]["android"] = "today"
        if extra_shot:
            data["screens"]["list"]["settings"]["droid"] = {"shot": "settings", "env": {"LACHING_SCREENSHOT_MODE": "two"}}
        config.write_text(json.dumps(data), "utf8")

    def test_check_layout_android_boots_an_emulator_and_runs_the_languages_on_it(self):
        self.android_config()
        state = self.root / "device"  # the emulator's files/screens, and the marker `emu kill` leaves
        state.mkdir()
        fake_adb = self.root / "fake_adb.py"
        # adb against a device that is up at once: the app writes its shots (plus problems.txt in German) into
        # files/screens when started in screenshot mode; `emu kill` tells the fake emulator to exit, `emu avd snapshot
        # save` keeps the device's properties (ram.json) as the snapshot's, for the fake emulator to load.
        fake_adb.write_text(f"""import json, shutil, sys, time
from pathlib import Path
state = Path({str(state)!r})
screens = state / "files" / "screens"
ram = state / "ram.json"
args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
def log(line):
    with open({str(self.log)!r}, "a") as out:
        out.write(line + "\\n")
command = args[0]
if command == "install":
    log("install " + Path(args[-1]).name)
elif command == "emu" and args[1:2] == ["posture"]:
    log("posture " + args[2])
    print("OK")
elif command == "emu" and args[1:4] == ["avd", "snapshot", "save"]:
    (state / "snapshots").mkdir(exist_ok=True)
    shutil.copy(ram, state / "snapshots" / args[4])
    log("save " + args[4])
    print("OK")
elif command == "emu":
    (state / "killed").touch()
    log("kill")
elif command == "exec-out":
    sys.stdout.buffer.write((screens / Path(args[-1]).name).read_bytes())
elif command == "shell":
    words = args[1:]
    if words[:2] == ["getprop", "sys.boot_completed"]:
        print("1")
    elif words[:1] == ["date"]:
        print(int(time.time()))
    elif words[:1] == ["getprop"]:
        print(json.loads(ram.read_text()).get(words[1], ""))
    elif words[:1] == ["setprop"]:
        ram.write_text(json.dumps({{**json.loads(ram.read_text()), words[1]: words[2]}}))
    elif words[:3] == ["settings", "put", "system"]:
        log(" ".join(words[3:]))
    elif words[:2] == ["wm", "user-rotation"]:
        log(" ".join(words[1:]))
    elif words[:2] == ["wm", "size"]:
        print("Physical size: 2560x1600")
    elif words[:2] == ["cmd", "device_state"]:
        print("Committed state: DeviceState{{identifier=2, name='OPENED', app_accessible=true}}")
    elif words[:2] == ["cmd", "package"]:
        print("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true")
        print("app.fixture.dev/.Main")
    elif words[:2] == ["am", "start"]:
        value = lambda key: words[words.index(key) + 1]
        language = value("LACHING_LANG")
        log(f"start {{language}} {{value('LACHING_REGION')}} {{value('LACHING_SCREENSHOT_MODE')}} {{value('LACHING_SCREENS')}}")
        screens.mkdir(parents=True, exist_ok=True)
        for shot in value("LACHING_SCREENS").split(","):
            for theme in ("light", "dark"):
                (screens / f"{{shot}}-{{theme}}.png").write_bytes(b"png")
        if language == "de":
            with open(screens / "problems.txt", "a", encoding="utf8") as problems:
                problems.write(f"{{value('LACHING_SCREENS')}}-light.png: “Wiederholungen” is cut short\\n")
        (screens / "done").touch()
    elif words[:1] == ["run-as"]:
        sub = words[2:]
        if sub[:2] == ["rm", "-rf"]:
            shutil.rmtree(screens, ignore_errors=True)
        elif sub == ["ls", "files/screens/done"]:
            sys.exit(0 if (screens / "done").exists() else 1)
        elif sub == ["ls", "files/screens"]:
            print("\\n".join(sorted(p.name for p in screens.iterdir())))
""", "utf8")
        fake_emulator = self.root / "fake_emulator.py"
        # The emulator starts with the properties of the snapshot it loads, else none.
        fake_emulator.write_text(f"""import shutil, sys, time
from pathlib import Path
state = Path({str(state)!r})
args = sys.argv[1:]
with open({str(self.log)!r}, "a") as out:
    out.write("emulator " + " ".join(args) + "\\n")
saved = state / "snapshots" / (args[args.index("-snapshot") + 1] if "-snapshot" in args else "-")
(state / "ram.json").write_bytes(saved.read_bytes() if saved.is_file() else b"{{}}")
while not (state / "killed").exists():
    time.sleep(0.1)
""", "utf8")
        self.fake_python("adb", fake_adb)
        self.fake_python("emulator", fake_emulator)
        apk = self.root / "app-debug.apk"
        apk.write_bytes(b"apk")

        avds = self.root / "avd"  # the AVD's folder, where the snapshot's marker goes
        (avds / "Pixel_8.avd").mkdir(parents=True)
        env = {"ANDROID_AVD_HOME": str(avds)}

        result = self.run_script("check-layout-android.py", "--apk", str(apk), "--devices", "Pixel_8", "--font-scale", "2.0",
                                 "--posture", "open", "--orientation", "portrait", "en", "de", env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("==")],
                         ["== android-Pixel_8 en", "== android-Pixel_8 de"])
        self.assertIn("settings-light.png: “Wiederholungen” is cut short", result.stdout, result.stderr)
        self.assertIn("today-light.png: “Wiederholungen” is cut short", result.stdout)
        self.assertIn("Layout problems in: android-Pixel_8/de", result.stderr)
        checked = self.root / "macos" / "build" / "layout-check" / "android-Pixel_8"
        self.assertTrue((checked / "en" / "today-light.png").exists())
        self.assertTrue((checked / "en" / "settings-dark.png").exists())
        self.assertEqual(len((checked / "de" / "problems.txt").read_text("utf8").splitlines()), 2,
                         "both launches' problems, one file")
        self.assertTrue((checked / "emulator.log").exists())
        lines = self.log.read_text().splitlines()
        self.assertEqual([line for line in lines if line.startswith("emulator ")], [
            "emulator -avd Pixel_8 -port 5554 -no-window -no-audio -no-boot-anim -no-snapshot-load -no-snapshot-save "
            "-gpu swiftshader_indirect"])
        self.assertEqual([line for line in lines if line.startswith(
            ("start", "install", "font_scale", "posture", "user-rotation", "kill", "save"))], [
            "save laching-screens", "install app-debug.apk", "font_scale 2.0", "posture 3",
            "user-rotation lock 1",  # portrait on a display that is landscape at rest
            "start en US screens today", "start en US two settings", "start de DE screens today", "start de DE two settings",
            "font_scale 1.0", "user-rotation free", "kill",
        ])

        self.log.unlink()
        (state / "killed").unlink()
        passing = self.run_script("check-layout-android.py", "--apk", str(apk), "--devices", "Pixel_8", "--only", "today", "en",
                                  env=env)
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn("Every screen fits on Pixel_8 in: en", passing.stdout)
        lines = self.log.read_text().splitlines()
        self.assertEqual([line for line in lines if line.startswith(("emulator ", "save", "install", "start"))], [
            "emulator -avd Pixel_8 -port 5554 -no-window -no-audio -no-boot-anim -snapshot laching-screens "
            "-no-snapshot-save -gpu swiftshader_indirect",
            "install app-debug.apk", "start en US screens today",
        ], "the second run loads the snapshot, saves nothing, and installs the app again")

        self.log.unlink()
        (state / "killed").unlink()
        cold = self.run_script("check-layout-android.py", "--apk", str(apk), "--devices", "Pixel_8", "--only", "today",
                               "--cold", "en", env=env)
        self.assertEqual(cold.returncode, 0, cold.stdout + cold.stderr)
        lines = self.log.read_text().splitlines()
        self.assertIn("-no-snapshot-load -no-snapshot-save", lines[0])
        self.assertIn("save laching-screens", lines)

    def test_check_layout_android_needs_an_avd(self):
        self.android_config()
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        del data["screens"]["platforms"]["droid"]["avd"]
        config.write_text(json.dumps(data), "utf8")
        result = self.run_script("check-layout-android.py", "--apk", "x.apk", "en")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no AVD: --devices NAME", result.stderr)

    def test_check_layout_android_counts_an_empty_shot_as_not_drawn(self):
        spec = importlib.util.spec_from_file_location("check_layout_android", TOOLS / "check-layout-android.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        (self.root / "today-light.png").write_bytes(b"png")
        (self.root / "today-dark.png").write_bytes(b"")
        self.assertEqual(tool.problems_in(self.root, ["today", "help"]),
                         ["today-dark.png: not drawn", "help-light.png: not drawn", "help-dark.png: not drawn"])

    @unittest.skipIf(WINDOWS, "the fakes are sh scripts")
    def test_verify_runs_the_kotlin_checks(self):
        self.android_config(extra_shot=False)
        self.fake("ktlint", f'echo "ktlint $*" >> "{self.log}"\n')
        self.fake("java", f'echo "java $*" >> "{self.log}"\n')
        gradlew = self.root / "android" / "gradlew"
        gradlew.parent.mkdir()
        gradlew.write_text(f'#!/bin/sh\necho "gradlew $*" >> "{self.log}"\n', "utf8")
        gradlew.chmod(0o755)
        source = self.root / "android" / "core" / "src" / "main" / "kotlin" / "A.kt"
        source.parent.mkdir(parents=True)
        source.write_text("fun a() = 1\n", "utf8")

        fast = self.run_script("verify.sh", "--fast", "--", "android/core/src/main/kotlin/A.kt")
        self.assertEqual(fast.returncode, 0, fast.stdout + fast.stderr)
        self.assertIn("ok    ktlint", fast.stdout)
        self.assertIn("ktlint android/core/src/main/kotlin/A.kt", self.log.read_text())
        self.assertNotIn("gradlew", self.log.read_text(), "--fast leaves the tests")

        self.log.unlink()
        slow = self.run_script("verify.sh", "--", "android/core/src/main/kotlin/A.kt")
        self.assertEqual(slow.returncode, 0, slow.stdout + slow.stderr)
        self.assertIn("ok    gradlew :core:test", slow.stdout)
        self.assertIn("gradlew :core:test -q --console=plain", self.log.read_text())
        vectors = self.run_script("verify.sh", "--", "conformance/phrases.json")
        self.assertIn("ok    gradlew :core:test", vectors.stdout, "the vectors are what :core replays")

        (self.bin / "ktlint").unlink()
        gradlew.unlink()
        # $PATH without the folders that hold a real ktlint (Homebrew's, on the maintainer's Mac).
        without = [f for f in os.environ["PATH"].split(os.pathsep) if not (Path(f) / "ktlint").exists()]
        path = os.pathsep.join([str(self.bin), str(Path(sys.executable).parent), *without])
        skipped = self.run_script("verify.sh", "--", "android/core/src/main/kotlin/A.kt", env={"PATH": path})
        self.assertIn("skip  ktlint: brew install ktlint, or setup-linux.sh", skipped.stdout)
        self.assertIn("skip  gradlew :core:test: needs android/gradlew and a JDK 17 (setup-linux.sh)", skipped.stdout)

    def test_verify_runs_what_the_paths_need(self):
        result = self.run_script("verify.sh", "--fast", "--", "conformance/phrases.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            [line.split(None, 1)[1] for line in result.stdout.splitlines() if line.startswith("ok")],
            ["Laching pins agree", "Laching's tools, not copies (companion.py conform)", "phrases.py check", "voice.py check"],
        )
        strings = self.run_script("verify.sh", "--fast", "--", "macos/Fixture/Resources/Localizable.xcstrings")
        self.assertEqual(strings.returncode, 1, "the fixture's German has gaps")
        self.assertIn("FAIL  l10n.py check", strings.stdout)
        self.assertIn("de: app 3", strings.stdout)
        quiet = self.run_script("verify.sh", "--", "notes.md")
        self.assertEqual(quiet.returncode, 0, quiet.stdout)
        self.assertEqual(quiet.stdout.strip().splitlines(),
                         ["ok    Laching pins agree", "ok    Laching's tools, not copies (companion.py conform)"])

    def test_verify_runs_the_apps_own_checks(self):
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["verify"] = {"checks": [
            {"name": "notes are short", "paths": "^notes/", "run": "test $(wc -l < notes/a.md) -lt 3"},
            {"name": "the slow one", "paths": "^notes/", "run": "echo ran > slow.txt", "slow": True},
        ]}
        config.write_text(json.dumps(data), "utf8")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "a.md").write_text("one\n", "utf8")
        fast = self.run_script("verify.sh", "--fast", "--", "notes/a.md")
        self.assertEqual(fast.returncode, 0, fast.stdout + fast.stderr)
        self.assertIn("ok    notes are short", fast.stdout)
        self.assertNotIn("the slow one", fast.stdout)
        other = self.run_script("verify.sh", "--fast", "--", "elsewhere.md")
        self.assertNotIn("notes are short", other.stdout, "only when its paths change")
        (self.root / "notes" / "a.md").write_text("1\n2\n3\n", "utf8")
        slow = self.run_script("verify.sh", "--", "notes/a.md")
        self.assertEqual(slow.returncode, 1)
        self.assertIn("FAIL  notes are short", slow.stdout)
        self.assertIn("ok    the slow one", slow.stdout)
        self.assertEqual((self.root / "slow.txt").read_text().strip(), "ran", "run from the app's root")

    def test_verify_all_skips_what_the_app_lacks(self):
        for tool in ("xcrun", "xcodegen", "dotnet"):
            self.fake(tool, "exit 0\n")
        store_line = lambda result: [line for line in result.stdout.splitlines() if "store listing" in line]
        self.assertEqual(store_line(self.run_script("verify.sh", "--all", "--fast")), ["ok    store listing (store/check.py)"])
        (self.root / "store-listing" / "en.md").unlink()
        self.assertEqual(
            store_line(self.run_script("verify.sh", "--all", "--fast")), ["skip  store listing: no store-listing/en.md yet"]
        )
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        del data["store"]
        config.write_text(json.dumps(data), "utf8")
        result = self.run_script("verify.sh", "--all", "--fast")
        self.assertIn("ok    Laching pins agree", result.stdout, result.stderr)
        self.assertEqual(store_line(result), [], "no store section: --all has no store check to run")

    def test_verify_checks_the_generators_outputs(self):
        from test_character import write_skin

        ran = lambda result: [line for line in result.stdout.splitlines() if "--check" in line]
        recipes = self.run_script("verify.sh", "--fast", "--", "macos/scripts/sounds.py")
        self.assertEqual(ran(recipes), ["FAIL  synth.py --check"], "the fixture's sounds aren't written yet")
        self.assertEqual(self.run_script("synth.py").returncode, 0)
        wav = "macos/Fixture/Resources/Sounds/blip.wav"
        self.assertEqual(ran(self.run_script("verify.sh", "--fast", "--", wav)), ["ok    synth.py --check"])
        (self.root / wav).write_bytes(b"RIFF")
        self.assertEqual(ran(self.run_script("verify.sh", "--fast", "--", wav)), ["FAIL  synth.py --check"])
        self.assertEqual(ran(self.run_script("verify.sh", "--fast", "--", "laching")), ["FAIL  synth.py --check"],
                         "a pin bump can change the generator")

        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["characters"] = {"design": "design/characters", "swiftOut": "macos/FixtureCore/Sources/FixtureCore/Art"}
        config.write_text(json.dumps(data), "utf8")
        write_skin(self.root / "design" / "characters", "tiny")
        self.assertEqual(self.run_script("character.py").returncode, 0)
        rig = "design/characters/tiny/tiny.rig.json"
        self.assertEqual(ran(self.run_script("verify.sh", "--fast", "--", rig)), ["ok    character.py --check"])
        art = "macos/FixtureCore/Sources/FixtureCore/Art/TinyArt.swift"
        (self.root / art).write_text("// edited by hand\n", "utf8")
        for path in (art, "conformance/characters/tiny.json", rig):
            self.assertEqual(ran(self.run_script("verify.sh", "--fast", "--", path)), ["FAIL  character.py --check"], path)
        self.assertEqual(ran(self.run_script("verify.sh", "--fast", "--", "notes.md")), [])

    def commit_app(self) -> None:
        """The fixture app as a repository, with laching/ a checkout of its own on the pinned tag."""
        laching = self.root / "laching"
        laching.mkdir()
        git(laching, "init", "-q")
        git(laching, "commit", "-q", "--allow-empty", "-m", "release")
        git(laching, "tag", "v0.5.0")
        git(self.root, "init", "-q")
        (self.root / ".gitignore").write_text("laching/\nfake-bin/\ncalls.log\n", "utf8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "app")

    @unittest.skipUnless(WINDOWS, "Windows builds the WinUI app itself; elsewhere it's the VM's")
    def test_verify_builds_the_winui_app_itself_on_windows(self):
        self.commit_app()
        self.fake("dotnet", f'echo "$*" >> "{self.log}"\n')
        result = self.run_script("verify.sh", "--", "windows/Fixture.App/Views/HomeView.xaml")
        self.assertIn("ok    WinUI app builds (Fixture.App)", result.stdout, result.stderr)
        self.assertIn("build ", self.log.read_text())

    @unittest.skipIf(WINDOWS, "on Windows verify.sh builds the WinUI app itself and never asks for the Mac's VM")
    def test_verify_builds_the_winui_app_in_the_vm_when_there_is_one(self):
        self.commit_app()
        self.fake("dotnet", "exit 0\n")
        app = "windows/Fixture.App/Views/HomeView.xaml"
        self.fake("prlctl", 'echo "Failed to get VM config: The virtual machine could not be found."; exit 255\n')
        result = self.run_script("verify.sh", "--", app, env={"HOME": str(self.root)})
        self.assertIn("skip  WinUI app: builds on Windows or in the Parallels VM (Parallels has no VM named "
                      "'Windows 11'", result.stdout, result.stderr)
        # With a VM, vm.sh builds it (this fake one runs nothing, so the copy into it fails).
        self.fake("prlctl", f'''printf '%s\\n' "$*" >> "{self.log}"
case "$1" in status) echo "VM $2 exist running" ;; exec) [ "$3" = --current-user ] && [ "$4" = cmd ] || exit 1 ;; esac
''')
        result = self.run_script("verify.sh", "--", app, env={"HOME": str(self.root)})
        self.assertIn("FAIL  WinUI app builds in the VM (Fixture.App, x64 + ARM64)", result.stdout, result.stderr)
        self.assertIn("the copy into the VM failed", result.stdout)
        self.assertIn("powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \\\\Mac\\AllFiles",
                      self.log.read_text())

    def test_verify_reads_the_submodules_under_a_git_hook(self):
        self.commit_app()
        # What git exports to a pre-commit hook in a linked worktree: absolute paths into the app's repository.
        hook = {"GIT_DIR": str(self.root / ".git"), "GIT_INDEX_FILE": str(self.root / ".git" / "index")}
        result = self.run_script("verify.sh", "--fast", "--", "notes.md", env=hook)
        self.assertEqual(result.stdout.strip().splitlines(),
                         ["ok    Laching pins agree", "ok    Laching's tools, not copies (companion.py conform)"],
                         result.stdout + result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "verify.sh runs xcodegen on macOS only")
    def test_verify_names_a_group_named_after_the_checkout(self):
        project = self.root / "macos" / "Fixture.xcodeproj" / "project.pbxproj"
        project.parent.mkdir()
        project.write_text("// generated\n", "utf8")
        self.commit_app()
        # XcodeGen's group for a source outside mac.dir, named after the folder the app is checked out in ("app").
        self.fake("xcodegen", f"printf '\\t\\tname = app;\\n' >> '{project}'\n")
        self.fake("xcrun", "exit 0\n")
        spec = self.root / "macos" / "project.yml"
        spec.write_text(spec.read_text("utf8") + "\n", "utf8")
        git(self.root, "add", "macos/project.yml")
        result = self.run_script("verify.sh", "--fast", "--staged")
        self.assertIn("FAIL  xcodegen (project.pbxproj in sync)", result.stdout, result.stderr)
        self.assertIn("named after this checkout's folder (app)", result.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "ditto and lsregister are macOS tools")
    def test_install_dev_copies_the_debug_build(self):
        built = self.root / "products"
        (built / "Fixture.app" / "Contents").mkdir(parents=True)
        home = self.root / "home"
        env = {"CONFIGURATION": "Debug", "BUILT_PRODUCTS_DIR": str(built), "FULL_PRODUCT_NAME": "Fixture.app",
               "PROJECT_DIR": str(self.root / "macos"), "HOME": str(home), "CI": ""}
        result = self.run_script("install-dev.sh", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((home / "Applications" / "Fixture Dev.app" / "Contents").is_dir())
        release = self.run_script("install-dev.sh", env={**env, "CONFIGURATION": "Release", "HOME": str(self.root / "h2")})
        self.assertEqual(release.returncode, 0)
        self.assertFalse((self.root / "h2").exists(), "Release builds are skipped")


class FontsPlanTests(unittest.TestCase):
    def test_the_plan_comes_from_companion_json(self):
        data = json.loads((HERE / "sunnysays.companion.json").read_text("utf8"))
        root = Path("/app").resolve()  # the Config resolves its paths: Q:\app on Windows
        plan = fonts.Plan(companion.Config(data, root))
        self.assertEqual(plan.out, root / "macos/SunnySays/Resources/Fonts")
        self.assertEqual(plan.path("Medium"), plan.out / "Fredoka-Medium.ttf")
        self.assertEqual(plan.weights, {"Medium": 500, "SemiBold": 600})
        self.assertEqual(plan.compose["Ș"], ["S", "comma", "below", "Ş"])
        self.assertEqual(plan.license_file, "OFL-Fredoka.txt")
        self.assertEqual(fonts.glyph_name("ț"), "uni021B")
        self.assertIn("for sunnysays", plan.description())
        self.assertIn("no kerning after the space", plan.description())

    def test_more_faces_share_the_license_and_the_checks(self):
        data = json.loads((HERE / "sunnysays.companion.json").read_text("utf8"))
        data["fonts"]["faces"] = [{"family": "FredokaUpright", "font": ["https://example.com/u.ttf", "0" * 64],
                                   "weights": {"Bold": 700}}]
        self.assertEqual(companion.validate(data), [])
        plan = fonts.Plan(companion.Config(data, Path("/app")))
        self.assertEqual([face.family for face in plan.faces], ["Fredoka", "FredokaUpright"])
        self.assertEqual(plan.faces[1].path("Bold"), plan.out / "FredokaUpright-Bold.ttf")
        self.assertEqual(plan.faces[1].font, ("https://example.com/u.ttf", "0" * 64))
        self.assertEqual(plan.weights, {"Medium": 500, "SemiBold": 600})
        self.assertIn("Modified version of FredokaUpright", plan.description("FredokaUpright"))
        data["fonts"]["faces"][0].pop("weights")
        self.assertIn("weights", "\n".join(companion.validate(data)))

    def test_no_fonts_section(self):
        with fixture_app():
            with self.assertRaises(SystemExit):
                fonts.Plan(companion.load())


# A font every Mac has, else the one most Linux machines have.
FONT = next((font for font in (Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                               Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                               Path("C:/Windows/Fonts/arial.ttf")) if font.exists()), None)


class StoreScreenshotTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("python3") and FONT, "needs Arial or DejaVu Sans")
    def test_composes_a_store_image(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("needs Pillow (pip3 install pillow)")
        with fixture_app() as root:
            data = json.loads((root / "companion.json").read_text("utf8"))
            data["screenshots"]["store"] = {"captions": "captions.json", "out": "store", "size": [1440, 900], "font": str(FONT)}
            (root / "companion.json").write_text(json.dumps(data), "utf8")
            (root / "captions.json").write_text(json.dumps(
                {"en": [{"shot": "panel", "appearance": "dark", "headline": ["Fixy says hi"], "note": "A fixture."}],
                 "it": [{"shot": "panel", "appearance": "dark", "headline": ["Ciao da Fixy"], "note": "Una prova."}]}), "utf8")
            png(root / "screenshots" / "macos" / "panel-dark.png", 200)
            result = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py")], cwd=root,
                                    capture_output=True, encoding="utf8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "store" / "en" / "1-panel-dark.png").exists())

            # Another language from captures given; a captures folder is one language's; a language needs captions.
            italian = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--lang", "it",
                                      str(root / "screenshots" / "macos")], cwd=root,
                                      capture_output=True, encoding="utf8")
            self.assertEqual(italian.returncode, 0, italian.stderr)
            self.assertEqual(italian.stdout.split(), ["store/it/1-panel-dark.png"])
            both = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--lang", "all",
                                   str(root / "screenshots" / "macos")], cwd=root, capture_output=True, encoding="utf8")
            self.assertEqual(both.returncode, 2)
            self.assertIn("a captures folder is one language's", both.stderr)
            unknown = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--lang", "fr"], cwd=root,
                                     capture_output=True, encoding="utf8")
            self.assertIn("no captions for fr in store.captions (it has en, it)", unknown.stderr)

            # The Android canvases from a phone's captures: the phone image is portrait, with Play's limits checked.
            from PIL import Image
            png(root / "build" / "screens" / "droid" / "en" / "panel-dark.png", 200)
            captures = str(root / "build" / "screens" / "droid" / "en")
            phone = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "android-phone", captures],
                                   cwd=root, capture_output=True, encoding="utf8")
            self.assertEqual(phone.returncode, 0, phone.stderr)
            self.assertEqual(Image.open(root / "store" / "android-phone" / "en" / "1-panel-dark.png").size, (1080, 1920))
            tablet = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "android-tablet",
                                     "--out", str(root / "tablet"), captures], cwd=root,
                                     capture_output=True, encoding="utf8")
            self.assertEqual(tablet.returncode, 0, tablet.stderr)
            self.assertEqual(Image.open(root / "tablet" / "en" / "1-panel-dark.png").size, (2560, 1600))
            data["screenshots"]["store"]["canvases"] = {"android-phone": [1080, 2400]}
            (root / "companion.json").write_text(json.dumps(data), "utf8")
            tall = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "android-phone", captures],
                                  cwd=root, capture_output=True, encoding="utf8")
            self.assertEqual(tall.returncode, 1)
            self.assertIn("1080×2400: Google Play takes no side more than 2× the other", tall.stderr)


class MicrosoftStoreScreenshotTests(FixtureAppTestCase):
    """--canvas microsoft: Windows captures centered unscaled on 3840 × 2160, from synthetic windows."""

    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("needs Pillow (pip3 install pillow)")
        super().setUp()
        self.Image = Image
        self.config_path = self.root / "companion.json"
        self.data = json.loads(self.config_path.read_text("utf8"))
        self.data["store"]["microsoft"].update({"shots": ["panel-light", "home-dark"], "backdrop": {"light": "#102030"}})
        self.write_config()
        self.captures = self.root / "screenshots" / "windows"
        self.window(self.captures / "panel-light.png", (400, 300))
        self.window(self.captures / "home-dark.png", (601, 451), body=(30, 30, 30))
        self.out = self.root / "store-listing" / "microsoft" / "screenshots"

    def write_config(self):
        self.config_path.write_text(json.dumps(self.data), "utf8")

    def window(self, path: Path, size, body=(250, 248, 240), content=True):
        """A window: a title band, a body and, unless `content` is off, something drawn in it."""
        image = self.Image.new("RGB", size, body)
        image.paste((200, 200, 200), (0, 0, size[0], 32))
        if content:
            image.paste((240, 180, 40), (size[0] // 3, size[1] // 2, size[0] // 2, size[1] - 20))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def run_tool(self, *args: str):
        return subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "microsoft", *args],
                              cwd=self.root, capture_output=True, encoding="utf8")

    def test_centers_each_capture_unscaled_in_upload_order(self):
        stale = self.out / "en" / "9-gone-light.png"
        self.window(stale, (100, 100))
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "store-listing/microsoft/screenshots/en/1-panel-light.png (400×300 window)",
            "store-listing/microsoft/screenshots/en/2-home-dark.png (601×451 window)",
        ])
        self.assertEqual(sorted(path.name for path in (self.out / "en").iterdir()), ["1-panel-light.png", "2-home-dark.png"])
        for name, capture, backdrop in (("1-panel-light", "panel-light", (0x10, 0x20, 0x30)),
                                        ("2-home-dark", "home-dark", (0x1C, 0x1C, 0x1E))):
            image = self.Image.open(self.out / "en" / f"{name}.png").convert("RGB")
            window = self.Image.open(self.captures / f"{capture}.png").convert("RGB")
            self.assertEqual(image.size, (3840, 2160))
            x, y = (3840 - window.width) // 2, (2160 - window.height) // 2
            self.assertEqual(image.crop((x, y, x + window.width, y + window.height)).tobytes(), window.tobytes(), name)
            # The backdrop of its appearance: store.microsoft.backdrop, else the store palette's paper.
            self.assertEqual(image.getpixel((0, 0)), backdrop)
            # A shadow under the window.
            self.assertLess(sum(image.getpixel((x + window.width // 2, y + window.height + 20))), sum(backdrop))

    def test_a_hard_shadow(self):
        self.data["store"]["microsoft"]["shadow"] = {"offset": 16, "light": "#FF4D9D", "dark": "#D8709F"}
        self.write_config()
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        image = self.Image.open(self.out / "en" / "1-panel-light.png").convert("RGB")
        window = self.Image.open(self.captures / "panel-light.png").convert("RGB")
        # The window and its shadow centered together, the shadow a block of the theme's color 16 px right and down.
        x, y = (3840 - window.width - 16) // 2, (2160 - window.height - 16) // 2
        self.assertEqual(image.crop((x, y, x + window.width, y + window.height)).tobytes(), window.tobytes())
        self.assertEqual(image.getpixel((x + window.width + 8, y + window.height + 8)), (0xFF, 0x4D, 0x9D))
        self.assertEqual(image.getpixel((x + window.width + 20, y + window.height + 20)), (0x10, 0x20, 0x30), "hard: no blur")
        dark = self.Image.open(self.out / "en" / "2-home-dark.png").convert("RGB")
        self.assertEqual(dark.getpixel(((3840 + 601 + 16) // 2 - 4, (2160 + 451 + 16) // 2 - 4)), (0xD8, 0x70, 0x9F))
        # The shadow counts against the canvas.
        self.window(self.captures / "home-dark.png", (3830, 400))
        large = self.run_tool()
        self.assertEqual(large.returncode, 1)
        self.assertIn("larger than the 3840×2160 canvas", large.stderr)

    def test_refuses_a_bad_capture_before_writing(self):
        first = self.run_tool()
        self.assertEqual(first.returncode, 0, first.stderr)
        made = sorted((self.out / "en").iterdir())

        # Captured before it drew: a title bar over one flat color.
        self.window(self.captures / "home-dark.png", (600, 450), body=(0, 0, 0), content=False)
        blank = self.run_tool()
        self.assertEqual(blank.returncode, 1)
        self.assertIn("screenshots/windows/home-dark.png is one flat color below its top quarter", blank.stderr)
        # Larger than the canvas (never scaled down).
        self.window(self.captures / "home-dark.png", (3900, 400))
        large = self.run_tool()
        self.assertEqual(large.returncode, 1)
        self.assertIn("is 3900×400, larger than the 3840×2160 canvas", large.stderr)
        # Missing.
        (self.captures / "home-dark.png").unlink()
        missing = self.run_tool()
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no screenshots/windows/home-dark.png (store.microsoft.shots)", missing.stderr)
        self.assertEqual(sorted((self.out / "en").iterdir()), made, "nothing is touched until every capture is good")

    def test_folders_and_languages(self):
        # A captures folder for one other language, into --out.
        italian = self.root / "captures-it"
        for shot in ("panel-light", "home-dark"):
            self.window(italian / f"{shot}.png", (300, 200))
        given = self.run_tool("--lang", "it", "--out", str(self.root / "elsewhere"), str(italian))
        self.assertEqual(given.returncode, 0, given.stderr)
        self.assertTrue((self.root / "elsewhere" / "it" / "2-home-dark.png").exists())
        # store.microsoft.screenshots moves the folder, as import.py reads it.
        self.data["store"]["microsoft"]["screenshots"] = "ms/shots"
        self.write_config()
        moved = self.run_tool()
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertTrue((self.root / "store-listing" / "ms" / "shots" / "en" / "1-panel-light.png").exists())
        # A language must be the app's, a folder is one language's, and store.microsoft needs its shots.
        unknown = self.run_tool("--lang", "fr")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("fr isn't in languages.all (en, it, de)", unknown.stderr)
        both = self.run_tool("--lang", "all", str(italian))
        self.assertIn("a captures folder is one language's", both.stderr)
        del self.data["store"]["microsoft"]["shots"]
        self.write_config()
        unset = self.run_tool()
        self.assertEqual(unset.returncode, 1)
        self.assertIn("store.microsoft: missing shots", unset.stderr, "the schema wants them")

    def test_shots_are_checked_by_the_schema(self):
        self.assertEqual(companion.validate(self.data), [])
        self.data["store"]["microsoft"]["shots"] = ["panel"]
        self.data["store"]["microsoft"]["backdrop"] = {"light": "white"}
        problems = companion.validate(self.data)
        self.assertIn("store.microsoft.shots[0]: 'panel' doesn't match -(light|dark)$", problems)
        self.assertIn("store.microsoft.backdrop.light: 'white' doesn't match ^#[0-9A-Fa-f]{6}$", problems)

    def load_tool(self):
        spec = importlib.util.spec_from_file_location("store_screenshots", TOOLS / "store-screenshots.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_captures_another_language_first(self):
        tool = self.load_tool()
        config = companion.load(self.root)
        folder = self.root / "build" / "store-captures" / "microsoft" / "it"
        shots = ["panel-light", "home-dark", "panel-dark"]
        self.assertEqual(tool.windows_capture_command(config, folder, "it", shots, windows=False), [
            str(TOOLS / "windows" / "vm.sh"), "screenshots", str(folder), "--",
            "-Only", "panel,home", "-Front", "-Language", "it",
        ])
        on_windows = tool.windows_capture_command(config, folder, "it", shots, windows=True)
        self.assertEqual(on_windows[:6], ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                          str(TOOLS / "windows" / "screenshots.ps1")])
        self.assertEqual(on_windows[6:], ["-Out", str(folder), "-Only", "panel,home", "-Front", "-Language", "it"])
        own = self.root / "windows" / "scripts" / "screenshots.ps1"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text("# the app's own\n", "utf8")
        self.assertEqual(tool.windows_capture_command(config, folder, "it", shots, windows=True)[5], str(own.resolve()))

        # A run for Italian clears the old captures, captures, then composes from them.
        self.window(folder / "panel-light.png", (50, 50))
        calls = []

        def capture(command):
            calls.append(command)
            self.assertFalse(any(folder.glob("*.png")), "an old capture must not stand in for a new one")
            for shot in ("panel-light", "home-dark"):
                self.window(folder / f"{shot}.png", (320, 240))
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(tool.subprocess, "run", capture), mock.patch.dict(os.environ, {"COMPANION_ROOT": str(self.root)}), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(tool.main(["--canvas", "microsoft", "--lang", "it"]), 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("store-listing/microsoft/screenshots/it/1-panel-light.png (320×240 window)", printed.getvalue())

        def failing(command):
            return subprocess.CompletedProcess(command, 3)

        with mock.patch.object(tool.subprocess, "run", failing), mock.patch.dict(os.environ, {"COMPANION_ROOT": str(self.root)}):
            with self.assertRaises(SystemExit) as stop:
                tool.main(["--canvas", "microsoft", "--lang", "de"])
        self.assertEqual(str(stop.exception), "store-screenshots: capturing the de windows failed")

    def captioned(self, style="zine"):
        """store.microsoft.captioned, with captions for en and it whose first entry Windows shows from another shot."""
        self.data["screenshots"]["store"] = {"captions": "captions.json", "out": "store", "font": str(FONT), "style": style,
                                             "palette": {"light": {"paper": "#F3EDE1", "ink": "#26336B", "accent": "#FF4D9D"}}}
        self.data["store"]["microsoft"]["captioned"] = True
        self.write_config()
        entry = {"shot": "menu", "headline": ["LIVES IN YOUR", "MENU BAR"], "note": "On your Mac.",
                 "windows": {"shot": "panel", "headline": ["LIVES IN YOUR", "TASKBAR"], "note": "On your PC."},
                 "ios": {"shot": "today"}}
        second = {"shot": "home", "appearance": "dark", "headline": ["HOME"], "note": "A note that wraps over lines."}
        (self.root / "captions.json").write_text(json.dumps({"en": [entry, second], "it": [entry, second]}), "utf8")

    def accent_pixels(self, path, right):
        left = self.Image.open(path).convert("RGB").crop((0, 0, right, 2160))
        return sum(count for count, color in left.getcolors(1 << 22) if color == (0xFF, 0x4D, 0x9D))

    @unittest.skipUnless(FONT, "needs Arial or DejaVu Sans")
    def test_captioned_lays_the_caption_beside_the_window(self):
        self.captioned()
        self.window(self.captures / "panel-light.png", (600, 1000))
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "store-listing/microsoft/screenshots/en/1-panel-light.png (600×1000 window)",
            "store-listing/microsoft/screenshots/en/2-home-dark.png (601×451 window)",
        ])
        path = self.out / "en" / "1-panel-light.png"
        image = self.Image.open(path).convert("RGB")
        self.assertEqual(image.size, (3840, 2160))
        self.assertEqual(image.getpixel((0, 0)), (0xF3, 0xED, 0xE1), "the palette's paper, not the backdrop")
        # The window on the right, enlarged no more than 1.5×, framed in ink over a hard shadow of ink on paper.
        width, height = 900, 1500
        x, y = 3840 - width - round(160 * 3840 / 2880), (2160 - height) // 2
        self.assertEqual(image.getpixel((x - 2, y + height // 2)), (0x26, 0x33, 0x6B))
        self.assertEqual(image.getpixel((x + width // 2, y + 4)), (200, 200, 200), "the title band, enlarged")
        self.assertEqual(image.getpixel((x + width + 20, y + height // 2)), (0xD2, 0xCF, 0xCE), "the ink at 16% on the paper")
        # The headline on the left, in ink over its accent offset.
        left = image.crop((0, 0, x - 160, 2160))
        self.assertIn((0x26, 0x33, 0x6B), {color for _, color in left.getcolors(1 << 22)})
        zine = self.accent_pixels(path, x - 160)
        # The dark shot takes the dark palette.
        self.assertEqual(self.Image.open(self.out / "en" / "2-home-dark.png").getpixel((0, 0))[:3], (0x1C, 0x1C, 0x1E))
        # The rule style: no offset copy, only the short rule in the accent.
        self.captioned("rule")
        self.assertEqual(self.run_tool().returncode, 0)
        self.assertLess(self.accent_pixels(path, x - 160), zine / 2)

    @unittest.skipUnless(FONT, "needs Arial or DejaVu Sans")
    def test_captioned_captions_go_one_to_one_with_the_shots(self):
        self.captioned()
        first = self.run_tool()
        self.assertEqual(first.returncode, 0, first.stderr)
        made = sorted((self.out / "en").iterdir())
        captions = json.loads((self.root / "captions.json").read_text("utf8"))
        del captions["en"][0]["windows"]
        (self.root / "captions.json").write_text(json.dumps(captions), "utf8")
        mismatched = self.run_tool()
        self.assertEqual(mismatched.returncode, 1)
        self.assertIn("the en captions show menu-light, home-dark, store.microsoft.shots panel-light, home-dark",
                      mismatched.stderr)
        self.assertEqual(sorted((self.out / "en").iterdir()), made, "nothing is touched")
        missing = self.run_tool("--lang", "de")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no captions for de in store.captions (store.microsoft.captioned)", missing.stderr)
        # A captioned window is scaled to fit, so it may be larger than the canvas.
        captions["en"][0]["windows"] = {"shot": "panel"}
        (self.root / "captions.json").write_text(json.dumps(captions), "utf8")
        self.window(self.captures / "panel-light.png", (3900, 2400))
        large = self.run_tool()
        self.assertEqual(large.returncode, 0, large.stderr)

    def test_captioned_is_checked_by_the_schema(self):
        self.data["screenshots"]["store"] = {"captions": "c.json", "out": "store", "style": "zine",
                                             "cjkFonts": {"ja": "fonts/NotoSansJP-Bold.ttf"}}
        self.data["store"]["microsoft"]["captioned"] = True
        self.assertEqual(companion.validate(self.data), [])
        self.data["screenshots"]["store"]["style"] = "fancy"
        self.data["store"]["microsoft"]["captioned"] = "yes"
        problems = companion.validate(self.data)
        self.assertTrue(any(problem.startswith("screenshots.store.style") for problem in problems), problems)
        self.assertTrue(any(problem.startswith("store.microsoft.captioned") for problem in problems), problems)


class IosStoreScreenshotTests(FixtureAppTestCase):
    """--canvas iphone|ipad|duo: the App Store's portrait images from the simulator's screens."""

    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("needs Pillow (pip3 install pillow)")
        if not FONT:
            self.skipTest("needs Arial or DejaVu Sans")
        super().setUp()
        self.Image = Image
        data = json.loads((self.root / "companion.json").read_text("utf8"))
        data["screenshots"]["store"] = {"captions": "captions.json", "out": "store", "font": str(FONT), "style": "zine",
                                        "palette": {"light": {"paper": "#F3EDE1", "ink": "#26336B", "accent": "#FF4D9D"}}}
        self.data = data
        self.write_config()
        entry = {"shot": "menu", "headline": ["LIVES IN YOUR", "MENU BAR"], "note": "On your Mac.",
                 "ios": {"shot": "today", "headline": ["NUDGES YOU"], "note": "Today shows your breaks."}}
        second = {"shot": "panel", "headline": ["PANEL"], "note": "A note."}
        (self.root / "captions.json").write_text(json.dumps({"en": [entry, second], "it": [entry, second]}), "utf8")

    def write_config(self):
        (self.root / "companion.json").write_text(json.dumps(self.data), "utf8")

    def screens(self, device, size, language="en"):
        """The simulator's screens: a black status bar over a body with a block drawn in it."""
        folder = self.root / "build" / "store-captures" / "ios" / device / language
        folder.mkdir(parents=True, exist_ok=True)
        for shot in ("today", "panel"):
            image = self.Image.new("RGB", size, (250, 248, 240))
            image.paste((0, 0, 0), (0, 0, size[0], 48))
            image.paste((240, 180, 40), (size[0] // 3, size[1] // 2, size[0] // 2, size[1] - 20))
            image.save(folder / f"{shot}-light.png")

    def run_tool(self, *args: str):
        return subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), *args], cwd=self.root,
                              capture_output=True, encoding="utf8")

    def test_each_device_from_its_captures_to_push_pys_folder(self):
        out = self.root / "store-listing" / "apple"
        for device, size in (("iphone", (1320, 2868)), ("ipad", (2064, 2752)), ("duo", (1398, 2034))):
            self.screens(device, size)
            stale = out / device / "en" / "9-gone-light.png"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_bytes(b"")
            result = self.run_tool("--canvas", device)
            self.assertEqual(result.returncode, 0, result.stderr)
            # The ios object over the entry; the images where push.py takes them, an earlier run's gone.
            self.assertEqual(result.stdout.split(), [f"store-listing/apple/{device}/en/1-today-light.png",
                                                     f"store-listing/apple/{device}/en/2-panel-light.png"])
            self.assertEqual(sorted(path.name for path in (out / device / "en").iterdir()),
                             ["1-today-light.png", "2-panel-light.png"])
            image = self.Image.open(out / device / "en" / "1-today-light.png").convert("RGB")
            self.assertEqual(image.size, size)
            self.assertEqual(image.getpixel((0, 0)), (0xF3, 0xED, 0xE1))
            # The screen centered below the caption, framed in ink; the iPad's status bar cut off, the others' kept.
            x, y = size[0] * 3 // 5, size[1] - 1
            for inside in (False, True, False):  # up to the bottom border, through it, up the screen to the top one
                while (image.getpixel((x, y)) == (0x26, 0x33, 0x6B)) == inside:
                    y -= 1
            self.assertEqual(image.getpixel((x, y + 1)), (250, 248, 240) if device == "ipad" else (0, 0, 0), device)

    def test_store_folders_and_missing_captures(self):
        self.data["store"]["apple"]["ios"] = {"iphoneScreenshots": "apple/phone"}
        self.write_config()
        self.screens("iphone", (1320, 2868))
        moved = self.run_tool("--canvas", "iphone")
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertTrue((self.root / "store-listing" / "apple" / "phone" / "en" / "1-today-light.png").exists())
        # Another language needs its own captures, and nothing is written without them.
        missing = self.run_tool("--canvas", "iphone", "--lang", "it")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no build/store-captures/ios/iphone/it/today-light.png: capture the app's iOS screens first",
                      missing.stderr)
        self.assertFalse((self.root / "store-listing" / "apple" / "phone" / "it").exists())

    def test_rule_style_and_canvas_size(self):
        self.data["screenshots"]["store"].update({"style": "rule", "canvases": {"duo": [2007, 2853]}})
        self.write_config()
        self.screens("duo", (1398, 2034))
        result = self.run_tool("--canvas", "duo", "--out", str(self.root / "elsewhere"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.Image.open(self.root / "elsewhere" / "en" / "1-today-light.png").size, (2007, 2853))


class CaptionWrapTests(unittest.TestCase):
    """store-screenshots.py's line breaking, measured by a fake draw: every character 10 px wide."""

    class Draw:
        @staticmethod
        def textlength(text, font=None):
            return 10 * len(text)

    @classmethod
    def setUpClass(cls):
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("needs Pillow (pip3 install pillow)")
        spec = importlib.util.spec_from_file_location("store_screenshots", TOOLS / "store-screenshots.py")
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def wrap(self, text, width, language):
        return self.tool.wrap(self.Draw(), text, None, width, language)

    def test_latin_and_korean_wrap_at_spaces_balanced(self):
        self.assertEqual(self.wrap("Pick the interval, hours and days.", 250, "en"),
                         ["Pick the interval,", "hours and days."])
        self.assertEqual(self.wrap("간격, 시간, 요일을 고르세요.", 100, "ko"), ["간격, 시간,", "요일을 고르세요."])

    def test_japanese_breaks_at_a_comma_else_after_a_particle(self):
        self.assertEqual(self.wrap("連続記録、週間のふり返り、CSV書き出し。", 130, "ja"),
                         ["連続記録、週間のふり返り、", "CSV書き出し。"])
        # No comma: after the particle that ends a phrase, and never inside a katakana word.
        self.assertEqual(self.wrap("休憩はリマインダーで知らせます", 80, "ja"), ["休憩は", "リマインダーで", "知らせます"])
        self.assertEqual(self.tool.pieces("「はい」。", "ja"), [("「は", ""), ("い」。", "")])

    def test_chinese_keeps_particles_and_spaced_words(self):
        self.assertEqual([piece for piece, _ in self.tool.pieces("CSV 导出的", "zh-Hans")], ["CSV", "导", "出的"])
        self.assertEqual(self.tool.pieces("CSV 导出", "zh-Hans")[1], ("导", " "))

    def test_platform_overrides(self):
        captions = [{"shot": "menu", "note": "Mac", "windows": {"note": "PC"}, "android": {"shot": "home"}}]
        self.assertEqual(self.tool.for_platform(captions, "microsoft")[0]["note"], "PC")
        self.assertEqual(self.tool.for_platform(captions, "android-phone")[0]["shot"], "home")
        self.assertEqual(self.tool.for_platform(captions, "mac"), captions)


class DevIconTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "AppKit: macOS only")
    def test_stamps_every_icon(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "Assets.xcassets"
            for size in (32, 128):
                png(catalog / "AppIcon.appiconset" / f"icon_{size}.png", size)
            (catalog / "AppIcon.appiconset" / "Contents.json").write_text('{"images": []}')
            result = subprocess.run(
                ["swift", str(TOOLS / "dev-icon.swift"), str(catalog), "#123456", "#FFFFFF"],
                capture_output=True, encoding="utf8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Wrote 2 icons", result.stdout)
            for size in (32, 128):
                stamped = (catalog / "AppIcon-Dev.appiconset" / f"icon_{size}.png").read_bytes()
                self.assertNotEqual(stamped, (catalog / "AppIcon.appiconset" / f"icon_{size}.png").read_bytes())
            # A fresh catalog gets the Dev set made, starting from AppIcon's Contents.json.
            self.assertEqual((catalog / "AppIcon-Dev.appiconset" / "Contents.json").read_text(), '{"images": []}')


class SharedFilesTests(unittest.TestCase):
    """The tools and skills are shared by every app: none names one, and every skill is well formed."""

    def test_no_app_is_named(self):
        names = re.compile(r"Sunny|sunnysays|Pagliacci|pagliacci|sunflower")
        for path in TOOLS.rglob("*"):
            parts = path.relative_to(TOOLS).parts
            # The landing package's installed dependencies and build output aren't ours.
            if "node_modules" in parts or parts[:2] == ("landing", "dist"):
                continue
            if path.is_file() and "tests" not in parts and "test" not in parts and path.suffix != ".pyc":
                self.assertIsNone(names.search(path.read_text("utf8", errors="replace")), path)

    def test_every_skill_has_frontmatter(self):
        skills = sorted(p.parent.name for p in (TOOLS / "skills").glob("*/SKILL.md"))
        self.assertEqual(skills, ["add-phrase", "add-setting", "conformance", "localization", "release-ios",
                                  "release-mac", "release-windows", "review-invariants", "voice", "windows-port"])
        for name in skills:
            text = (TOOLS / "skills" / name / "SKILL.md").read_text("utf8")
            header = text.split("---", 2)[1]
            self.assertIn(f"name: {name}\n", header)
            self.assertRegex(header, r"description: .{80,}")
        self.assertTrue((TOOLS / "skills" / "review-invariants" / "checklist.md").exists())


if __name__ == "__main__":
    unittest.main()
