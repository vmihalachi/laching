#!/usr/bin/env python3
"""Tests for the shell scripts and the small tools of tools/: conformance.sh, screenshots.sh and install-dev.sh run
against fake `swift` / `xcodebuild` commands over the fixture app; fonts.py's plan comes from companion.json;
store-screenshots.py and dev-icon.swift run when Pillow and a Mac with Swift are around."""

from __future__ import annotations

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

    def run_script(self, script: str, *args: str, env: dict | None = None, cwd: Path | None = None):
        if script.endswith(".sh") and BASH is None:
            self.skipTest(NO_SHELL)
        environment = {**os.environ, "PATH": search_path(self.bin), **(env or {})}
        return subprocess.run(
            [*shell(TOOLS / script), *args] if script.endswith(".sh") else [sys.executable, str(TOOLS / script), *args],
            cwd=cwd or self.root,
            env=environment, capture_output=True, text=True,
        )

    def test_every_shell_script_parses(self):
        for script in TOOLS.glob("*.sh"):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{script.name}: {result.stderr}")
            self.assertTrue(os.access(script, os.X_OK), f"{script.name} isn't executable")

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
echo "$LACHING_SCREENSHOT_MODE hidden=$LACHING_SCREENSHOT_HIDDEN $*" >> "{self.root / 'runs.log'}"
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
        # --only runs hidden unless --front; a full run comes forward; --build-only doesn't run the app.
        self.run_script("screenshots.sh", "--only", "panel", "--front", str(out), "it")
        self.run_script("screenshots.sh", str(out), "it")
        self.run_script("screenshots.sh", "--build-only")
        runs = (self.root / "runs.log").read_text().splitlines()
        self.assertEqual([line.split(" -Apple")[0] for line in runs], ["panel hidden=1", "panel hidden=0", "all hidden=0"])

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
        # files/screens when started in screenshot mode; `emu kill` tells the fake emulator to exit.
        fake_adb.write_text(f"""import shutil, sys
from pathlib import Path
state = Path({str(state)!r})
screens = state / "files" / "screens"
args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
def log(line):
    with open({str(self.log)!r}, "a") as out:
        out.write(line + "\\n")
command = args[0]
if command == "install":
    log("install " + Path(args[-1]).name)
elif command == "emu":
    (state / "killed").touch()
    log("kill")
elif command == "exec-out":
    sys.stdout.buffer.write((screens / Path(args[-1]).name).read_bytes())
elif command == "shell":
    words = args[1:]
    if words[:2] == ["getprop", "sys.boot_completed"]:
        print("1")
    elif words[:3] == ["settings", "put", "system"]:
        log(" ".join(words[3:]))
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
            with open(screens / "problems.txt", "a") as problems:
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
        self.fake("adb", f'exec "{sys.executable}" "{fake_adb}" "$@"\n')
        self.fake("emulator", f'echo "emulator $*" >> "{self.log}"\nwhile [ ! -f "{state}/killed" ]; do sleep 0.1; done\n')
        apk = self.root / "app-debug.apk"
        apk.write_bytes(b"apk")

        result = self.run_script("check-layout-android.py", "--apk", str(apk), "--devices", "Pixel_8", "--font-scale", "2.0",
                                 "en", "de")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("==")],
                         ["== android-Pixel_8 en", "== android-Pixel_8 de"])
        self.assertIn("settings-light.png: “Wiederholungen” is cut short", result.stdout)
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
            "emulator -avd Pixel_8 -port 5554 -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect"])
        self.assertEqual([line for line in lines if line.startswith(("start", "install", "font_scale", "kill"))], [
            "install app-debug.apk", "font_scale 2.0",
            "start en US screens today", "start en US two settings", "start de DE screens today", "start de DE two settings",
            "font_scale 1.0", "kill",
        ])

        self.log.unlink()
        (state / "killed").unlink()
        passing = self.run_script("check-layout-android.py", "--apk", str(apk), "--devices", "Pixel_8", "--only", "today", "en")
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn("Every screen fits on Pixel_8 in: en", passing.stdout)
        self.assertIn("start en US screens today", self.log.read_text())

    def test_check_layout_android_needs_an_avd(self):
        self.android_config()
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        del data["screens"]["platforms"]["droid"]["avd"]
        config.write_text(json.dumps(data), "utf8")
        result = self.run_script("check-layout-android.py", "--apk", "x.apk", "en")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no AVD: --devices NAME", result.stderr)

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
            ["Laching pins agree", "phrases.py check", "voice.py check"],
        )
        strings = self.run_script("verify.sh", "--fast", "--", "macos/Fixture/Resources/Localizable.xcstrings")
        self.assertEqual(strings.returncode, 1, "the fixture's German has gaps")
        self.assertIn("FAIL  l10n.py check", strings.stdout)
        self.assertIn("de: app 3", strings.stdout)
        quiet = self.run_script("verify.sh", "--", "notes.md")
        self.assertEqual(quiet.returncode, 0, quiet.stdout)
        self.assertEqual(quiet.stdout.strip(), "ok    Laching pins agree")

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
        self.fake("prlctl", f'''echo "$*" >> "{self.log}"
case "$1" in status) echo "VM $2 exist running" ;; exec) [ "$3" = --current-user ] && [ "$4" = cmd ] || exit 1 ;; esac
''')
        result = self.run_script("verify.sh", "--", app, env={"HOME": str(self.root)})
        self.assertIn("FAIL  WinUI app builds in the VM (Fixture.App, x64 + ARM64)", result.stdout, result.stderr)
        self.assertIn("the copy into the VM failed", result.stdout)
        self.assertIn("powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand",
                      self.log.read_text())

    def test_verify_reads_the_submodules_under_a_git_hook(self):
        self.commit_app()
        # What git exports to a pre-commit hook in a linked worktree: absolute paths into the app's repository.
        hook = {"GIT_DIR": str(self.root / ".git"), "GIT_INDEX_FILE": str(self.root / ".git" / "index")}
        result = self.run_script("verify.sh", "--fast", "--", "notes.md", env=hook)
        self.assertEqual(result.stdout.strip(), "ok    Laching pins agree", result.stdout + result.stderr)

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
                               Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")) if font.exists()), None)


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
            result = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py")], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "store" / "en" / "1-panel-dark.png").exists())

            # Another language from captures given; a captures folder is one language's; a language needs captions.
            italian = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--lang", "it",
                                      str(root / "screenshots" / "macos")], cwd=root, capture_output=True, text=True)
            self.assertEqual(italian.returncode, 0, italian.stderr)
            self.assertEqual(italian.stdout.split(), ["store/it/1-panel-dark.png"])
            both = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--lang", "all",
                                   str(root / "screenshots" / "macos")], cwd=root, capture_output=True, text=True)
            self.assertEqual(both.returncode, 2)
            self.assertIn("a captures folder is one language's", both.stderr)
            unknown = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--lang", "fr"], cwd=root,
                                     capture_output=True, text=True)
            self.assertIn("no captions for fr in store.captions (it has en, it)", unknown.stderr)

            # The Android canvases from a phone's captures: the phone image is portrait, with Play's limits checked.
            from PIL import Image
            png(root / "build" / "screens" / "droid" / "en" / "panel-dark.png", 200)
            captures = str(root / "build" / "screens" / "droid" / "en")
            phone = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "android-phone", captures],
                                   cwd=root, capture_output=True, text=True)
            self.assertEqual(phone.returncode, 0, phone.stderr)
            self.assertEqual(Image.open(root / "store" / "android-phone" / "en" / "1-panel-dark.png").size, (1080, 1920))
            tablet = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "android-tablet",
                                     "--out", str(root / "tablet"), captures], cwd=root, capture_output=True, text=True)
            self.assertEqual(tablet.returncode, 0, tablet.stderr)
            self.assertEqual(Image.open(root / "tablet" / "en" / "1-panel-dark.png").size, (2560, 1600))
            data["screenshots"]["store"]["canvases"] = {"android-phone": [1080, 2400]}
            (root / "companion.json").write_text(json.dumps(data), "utf8")
            tall = subprocess.run([sys.executable, str(TOOLS / "store-screenshots.py"), "--canvas", "android-phone", captures],
                                  cwd=root, capture_output=True, text=True)
            self.assertEqual(tall.returncode, 1)
            self.assertIn("1080×2400: Google Play takes no side more than 2× the other", tall.stderr)


class DevIconTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "AppKit: macOS only")
    def test_stamps_every_icon(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "Assets.xcassets"
            for size in (32, 128):
                png(catalog / "AppIcon.appiconset" / f"icon_{size}.png", size)
            (catalog / "AppIcon.appiconset" / "Contents.json").write_text('{"images": []}')
            result = subprocess.run(
                ["swift", str(TOOLS / "dev-icon.swift"), str(catalog), "#123456", "#FFFFFF"], capture_output=True, text=True)
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
        self.assertEqual(skills, ["add-phrase", "add-setting", "conformance", "localization", "release-mac",
                                  "review-invariants", "voice", "windows-port"])
        for name in skills:
            text = (TOOLS / "skills" / name / "SKILL.md").read_text("utf8")
            header = text.split("---", 2)[1]
            self.assertIn(f"name: {name}\n", header)
            self.assertRegex(header, r"description: .{80,}")
        self.assertTrue((TOOLS / "skills" / "review-invariants" / "checklist.md").exists())


if __name__ == "__main__":
    unittest.main()
