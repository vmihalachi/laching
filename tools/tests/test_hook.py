#!/usr/bin/env python3
"""Tests for tools/hooks/hook.py, which runs before and after every tool call in a companion app: a bad pattern there
quietly blocks or lets through things in every session. Runs the hook over the fixture app."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from support import BASH, NO_SHELL, REPO, TOOLS, WINDOWS, FixtureAppTestCase, companion, git, search_path, shell

HOOK = TOOLS / "hooks" / "hook.py"
VALIDATE = REPO / ".claude" / "hooks" / "validate.py"  # Laching's own Stop hook, beside the apps' hook.py


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hook():
    return load(HOOK)


def fake_verify(root, body):
    """A stand-in for the app's scripts/verify.sh wrapper, which the hook takes over Laching's; on Windows it runs
    through bash."""
    wrapper = root / "scripts" / "verify.sh"
    wrapper.parent.mkdir(exist_ok=True)
    wrapper.write_text("#!/bin/sh\n" + body, "utf8", newline="\n")
    wrapper.chmod(0o755)
    return wrapper


class HookTestCase(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()

    def run_hook(self, mode, event, payload=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root), "TMPDIR": self.tmp}
        return subprocess.run([sys.executable, str(HOOK), mode], input=payload if payload is not None else json.dumps(event),
                              encoding="utf8", capture_output=True, env=env, timeout=60)

    def configure(self, **hooks):
        """Adds `hooks` entries to the fixture app's companion.json."""
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["hooks"].update(hooks)
        path.write_text(json.dumps(data), "utf8")

    def blocked(self, tool, **tool_input):
        done = self.run_hook("pre", {"session_id": "t", "tool_name": tool, "tool_input": tool_input})
        self.assertIn(done.returncode, (0, 2), done.stderr)
        return done.returncode == 2


class PreToolUse(HookTestCase):
    def test_reads(self):
        root = self.root
        self.assertTrue(self.blocked("Read", file_path=str(root / "macos/Fixture/Resources/Localizable.xcstrings")))
        self.assertTrue(self.blocked("Read", file_path=str(root / "macos/Fixture.xcodeproj/project.pbxproj")))
        self.assertFalse(self.blocked("Read", file_path=str(root / "conformance/phrases.json")))
        self.assertFalse(self.blocked("Read", file_path=str(root / "companion.json")))
        self.assertTrue(self.blocked("Read", file_path=str(root / "voices/Voice/manifest.json")))
        self.assertFalse(self.blocked("Read", file_path=str(root / "macos/scripts/voice-config.json")))
        self.assertFalse(self.blocked("Read", file_path=str(root / "design/locked.txt")), "guardedExtra blocks edits only")

    def test_edits(self):
        root = self.root
        self.assertTrue(self.blocked("Edit", file_path=str(root / "conformance/phrases.json")))
        self.assertTrue(self.blocked("Write", file_path=str(root / "windows/Fixture.App/Strings/en/Resources.resw")))
        self.assertTrue(self.blocked("Write", file_path=str(root / "android/app/src/main/res/values-it/strings.xml")))
        self.assertFalse(self.blocked("Edit", file_path=str(root / "conformance/README.md")))
        self.assertFalse(self.blocked("Edit", file_path=str(root / "conformance/fixtures/manifest.json")), "handEdited")
        self.assertFalse(self.blocked("Edit", file_path=str(root / "conformance/plurals.json")), "a handEdited file")
        self.assertTrue(self.blocked("Edit", file_path=str(root / "conformance/plurals.json.bak")), "that file exactly")
        self.assertFalse(self.blocked("Edit", file_path=str(root / "macos/project.yml")))
        self.assertTrue(self.blocked("Write", file_path=str(root / "voices/Voice/it/clown/hello.m4a")))
        self.assertTrue(self.blocked("Edit", file_path=str(root / "voices/Voice/manifest.json")))
        self.assertTrue(self.blocked("Edit", file_path=str(root / "voices/review/it.html")))
        self.assertTrue(self.blocked("Write", file_path=str(root / "voices/LICENSE")))
        self.assertFalse(self.blocked("Edit", file_path=str(root / "macos/scripts/voice-direction.json")))
        self.assertTrue(self.blocked("Edit", file_path=str(root / "design/locked.txt")), "hooks.guardedExtra")

    def test_bash(self):
        xcstrings = "macos/Fixture/Resources/Localizable.xcstrings"
        self.assertTrue(self.blocked("Bash", command=f"cat {xcstrings}"))
        self.assertTrue(self.blocked("Bash", command=f"grep -C3 '\"Skip\"' {xcstrings}"))
        self.assertTrue(self.blocked("Bash", command=f"git diff {xcstrings}"))
        self.assertTrue(self.blocked("Bash", command=f"ls && head -50 {xcstrings}"))
        self.assertFalse(self.blocked("Bash", command=f"grep -n '\"Skip\"' {xcstrings}"))
        self.assertFalse(self.blocked("Bash", command=f"git diff --stat {xcstrings}"))
        self.assertFalse(self.blocked("Bash", command=f"git add {xcstrings} && git status"))
        self.assertFalse(self.blocked("Bash", command="python3 laching/tools/l10n.py status -v"))
        self.assertFalse(self.blocked("Bash", command="cat > notes.md <<'EOF'\nsee Localizable.xcstrings\nEOF"))
        self.assertFalse(self.blocked("Bash", command="cat companion.json"))

    def test_big_files_are_read_by_range(self):
        (self.root / "macos/Fixture/Views").mkdir(parents=True, exist_ok=True)
        big = self.root / "macos/Fixture/Views/Big.swift"
        big.write_text("".join(f"func f{i}() {{}}\n" for i in range(700)), "utf8")
        small = self.root / "macos/Fixture/Views/Small.swift"
        small.write_text("func f() {}\n" * 100, "utf8")
        done = self.run_hook("pre", {"session_id": "t", "tool_name": "Read", "tool_input": {"file_path": str(big)}})
        self.assertEqual(done.returncode, 2)
        self.assertIn("macos/Fixture/Views/Big.swift is 700 lines: read its outline first", done.stderr)
        self.assertFalse(self.blocked("Read", file_path=str(big), offset=100, limit=80))
        self.assertFalse(self.blocked("Read", file_path=str(big), limit=50))
        self.assertFalse(self.blocked("Read", file_path=str(small)))
        self.assertFalse(self.blocked("Read", file_path=str(self.root / "macos/Fixture/Views/Missing.swift")))
        self.assertTrue(self.blocked("Bash", command="cat macos/Fixture/Views/Big.swift"))
        self.assertTrue(self.blocked("Bash", command=f"cat {big} | grep func"))
        self.assertTrue(self.blocked("Bash", command="sed -n p macos/Fixture/Views/Big.swift"))
        self.assertTrue(self.blocked("Bash", command="sed 's/a/b/' macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="sed -n '100,180p' macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="sed -n '/^func f1/,/^}/p' macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="sed -i '' 's/a/b/' macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="head -40 macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="grep -n '^func' macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="wc -l macos/Fixture/Views/Big.swift"))
        self.assertFalse(self.blocked("Bash", command="cat > macos/Fixture/Views/Big.swift"), "a redirect target")
        self.assertFalse(self.blocked("Bash", command="cat macos/Fixture/Views/Small.swift"))
        self.configure(bigFileLines=0)
        self.assertFalse(self.blocked("Read", file_path=str(big)), "hooks.bigFileLines 0 disables")
        self.configure(bigFileLines=50)
        self.assertTrue(self.blocked("Read", file_path=str(small)))

    def test_grep(self):
        path = "macos/Fixture/Resources/Localizable.xcstrings"
        self.assertTrue(self.blocked("Grep", pattern="Skip", path=path, **{"-C": 3}))
        self.assertFalse(self.blocked("Grep", pattern="Skip", path=path))
        self.assertFalse(self.blocked("Grep", pattern="Skip", path="macos/Fixture", **{"-C": 3}))

    def test_codex_apply_patch(self):
        def patch(path):
            return f"*** Begin Patch\n*** Update File: {path}\n@@\n-a\n+b\n*** End Patch\n"

        self.assertTrue(self.blocked("apply_patch", command=patch("conformance/phrases.json")))
        self.assertTrue(self.blocked("apply_patch", command="*** Begin Patch\n*** Add File: notes.md\n+x\n"
                                     "*** Update File: macos/Fixture.xcodeproj/project.pbxproj\n*** End Patch\n"))
        self.assertFalse(self.blocked("apply_patch", command=patch("macos/project.yml")))
        below_root = {"session_id": "t", "cwd": str(self.root / "macos"), "tool_name": "apply_patch",
                      "tool_input": {"command": patch("../conformance/phrases.json")}}
        self.assertEqual(self.run_hook("pre", below_root).returncode, 2, "relative to the session's cwd")

    def test_copilot_shapes(self):
        xcstrings = str(self.root / "macos/Fixture/Resources/Localizable.xcstrings")
        self.assertTrue(self.blocked("Read", path=xcstrings))
        self.assertTrue(self.blocked("Edit", path=str(self.root / "conformance/phrases.json")))
        (self.root / "macos/Fixture/Views").mkdir(parents=True, exist_ok=True)
        big = self.root / "macos/Fixture/Views/Big.swift"
        big.write_text("func f() {}\n" * 700, "utf8")
        self.assertFalse(self.blocked("Read", path=str(big), view_range=[1, 50]))
        self.assertTrue(self.blocked("Read", path=str(big), view_range=[1, -1]))
        done = self.run_hook("pre", {"session_id": "t", "tool_name": "Bash",
                                     "tool_input": json.dumps({"command": f"cat {xcstrings}"})})
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(done.stdout)["permissionDecision"], "deny")

    def test_app_guards_name_their_reason_and_may_block_reads(self):
        self.configure(guards=[
            {"pattern": "^data/big-[^/]*\\.json$", "why": "generated: read data/index.json.", "reads": True},
            {"pattern": "^data/(?!index\\.json$)", "why": "generated by make-data."},
        ])
        big = str(self.root / "data/big-1.json")
        done = self.run_hook("pre", {"session_id": "t", "tool_name": "Read", "tool_input": {"file_path": big}})
        self.assertEqual(done.returncode, 2)
        self.assertIn("data/big-1.json: generated: read data/index.json.", done.stderr)
        self.assertTrue(self.blocked("Bash", command="head data/big-1.json"))
        self.assertFalse(self.blocked("Read", file_path=str(self.root / "data/small.json")), "edits only")
        self.assertTrue(self.blocked("Edit", file_path=str(self.root / "data/small.json")))
        self.assertFalse(self.blocked("Edit", file_path=str(self.root / "data/index.json")))

    def refusal(self, path):
        """The hook's answer to an Edit of `path`, "" when it allows it."""
        done = self.run_hook("pre", {"session_id": "t", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / path)}})
        self.assertIn(done.returncode, (0, 2), done.stderr)
        return done.stderr if done.returncode == 2 else ""

    def test_the_generators_outputs_name_their_command(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["characters"] = {"design": "design/characters", "swiftOut": "macos/FixtureCore/Sources/FixtureCore/Art"}
        path.write_text(json.dumps(data), "utf8")
        art = self.refusal("macos/FixtureCore/Sources/FixtureCore/Art/BigCatArt.swift")
        self.assertIn("laching/tools/character.py` from design/characters/", art)
        self.assertIn("laching/tools/character.py", self.refusal("conformance/characters/big-cat.json"),
                      "the character vectors name character.py, not conformance.sh")
        self.assertIn("laching/tools/conformance.sh", self.refusal("conformance/phrases.json"))
        self.assertIn("laching/tools/synth.py` from the recipes in macos/scripts/sounds.py",
                      self.refusal("macos/Fixture/Resources/Sounds/blip.wav"))
        dev = self.refusal("macos/Fixture/Resources/Assets.xcassets/AppIcon-Dev.appiconset/icon_16x16.png")
        self.assertIn("swift laching/tools/dev-icon.swift", dev)
        for allowed in (
            "design/characters/big-cat/big-cat.rig.json",  # the drawing is the source
            "macos/FixtureCore/Sources/FixtureCore/Art/Pose.swift",  # hand-written code next to the art
            "macos/scripts/sounds.py",
            "macos/Fixture/Resources/Sounds/README.md",
            "macos/Fixture/Resources/Assets.xcassets/AppIcon-Dev.appiconset/Contents.json",
        ):
            self.assertEqual(self.refusal(allowed), "", allowed)
        # A raster skin's assets are character.py's too; a vector app's assets folder is the app's.
        catalog = "macos/Fixture/Resources/Characters"
        self.assertEqual(self.refusal(f"{catalog}/big-cat-body.imageset/Contents.json"), "")
        data["characters"].update(mode="raster", assets=catalog, windowsAssets="windows/Fixture.App/Assets/Characters")
        path.write_text(json.dumps(data), "utf8")
        self.assertIn("character.py", self.refusal(f"{catalog}/big-cat-body.imageset/Contents.json"))
        self.assertIn("character.py", self.refusal("windows/Fixture.App/Assets/Characters/big-cat-body.scale-200.png"))
        self.assertEqual(self.refusal("windows/Fixture.App/Assets/Characters/README.md"), "")
        # Without the sections, nothing is guarded.
        del data["characters"], data["sounds"]
        path.write_text(json.dumps(data), "utf8")
        self.assertEqual(self.refusal("macos/FixtureCore/Sources/FixtureCore/Art/BigCatArt.swift"), "")
        self.assertEqual(self.refusal("macos/Fixture/Resources/Sounds/blip.wav"), "")

    def test_codex_argv_commands(self):
        xcstrings = "macos/Fixture/Resources/Localizable.xcstrings"
        self.assertTrue(self.blocked("Bash", command=["cat", xcstrings]))
        self.assertFalse(self.blocked("Bash", command=["git", "status"]))
        self.assertTrue(self.blocked("Bash", command=["bash", "-lc", f"ls && cat {xcstrings}"]), "the script is the command")
        self.assertFalse(self.blocked("Bash", command=["bash", "-lc", f"git add {xcstrings} && git status"]))

    def test_bad_input_and_a_bad_companion_json_fail_open(self):
        self.assertEqual(self.run_hook("pre", None, payload="not json").returncode, 0)
        for odd in ([1, 2], {"tool_name": "Read", "tool_input": 7}, {"tool_name": "apply_patch", "tool_input": None}):
            done = self.run_hook("pre", odd)
            self.assertEqual(done.returncode, 0, done.stderr)
        (self.root / "companion.json").write_text("{}", "utf8")
        event = {"session_id": "t", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / "conformance/phrases.json")}}
        self.assertEqual(self.run_hook("pre", event).returncode, 0)


class BaselineGuards(HookTestCase):
    """The guards an app has without a line for them in its companion.json, which only names the folders."""

    SHOT = "screenshots/macos/home-light.png"
    ICON = "macos/Fixture/Resources/Assets.xcassets/AppIcon.appiconset/icon_16x16.png"
    ASSETS = "windows/Fixture.App/Assets"

    def app(self, change=None):
        """The fixture app, set up after `change` to its companion.json."""
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        if change:
            change(data)
            path.write_text(json.dumps(data), "utf8")
        self.hook = load_hook()
        self.hook.setup(self.root)
        return data

    def edit(self, path):
        """Why an edit of `path` is refused, None when it isn't."""
        return self.hook.guarded(self.root / path, for_edit=True)

    def read(self, path):
        return self.hook.guarded(self.root / path, for_edit=False)

    def test_the_screenshot_folders_are_never_read_or_edited(self):
        self.app()
        self.assertIn("written by `laching/tools/screenshots.sh`", self.edit(self.SHOT))
        self.assertIn("(`laching/tools/screenshots.sh <folder>`)", self.read(self.SHOT), "a read too, with where to look")
        windows = self.read("screenshots/windows/fr/home-dark.png")
        self.assertIn("written by `laching/tools/windows/screenshots.ps1`", windows)
        self.assertIn("(`laching/tools/windows/vm.sh screenshots <folder>`)", windows)
        self.assertTrue(self.edit("screenshots/macos/problems.txt"), "whatever a run writes there")
        for own in ("screenshots/README.md", "screenshots/macos/README.md", "screenshots/ios/home.png",
                    "docs/screenshots/macos/home.png", "macos/scripts/screenshots.sh"):
            self.assertIsNone(self.edit(own), own)
            self.assertIsNone(self.read(own), own)
        # The hook as an agent runs it.
        shot = str(self.root / self.SHOT)
        self.assertTrue(self.blocked("Read", file_path=shot))
        self.assertTrue(self.blocked("Write", file_path=str(self.root / "screenshots/windows/home-light.png")))
        self.assertTrue(self.blocked("Bash", command=f"git diff {self.SHOT}"))
        self.assertFalse(self.blocked("Bash", command=f"git add {self.SHOT} && git status"))
        self.assertFalse(self.blocked("Bash", command="laching/tools/screenshots.sh --only home /tmp/shots"))
        self.assertFalse(self.blocked("Edit", file_path=str(self.root / "screenshots/README.md")))
        # The folders are companion.json's: another name moves the guard, no key leaves none.
        self.app(lambda data: (data["screenshots"].update(out="docs/shots"), data["windows"].pop("screenshots")))
        self.assertTrue(self.read("docs/shots/home-light.png"))
        self.assertIsNone(self.read(self.SHOT))
        self.assertIsNone(self.edit("screenshots/windows/home-light.png"))

    def test_the_app_icon_s_sizes_are_the_icon_script_s(self):
        self.app()
        self.assertIn("rendered by the app's own icon script", self.edit(self.ICON))
        self.assertIsNone(self.read(self.ICON), "edits only")
        self.assertTrue(self.blocked("Write", file_path=str(self.root / self.ICON)))
        self.assertFalse(self.blocked("Read", file_path=str(self.root / self.ICON)))
        catalog = self.ICON.rsplit("/", 2)[0]
        self.assertIn("dev-icon.swift", self.edit(f"{catalog}/AppIcon-Dev.appiconset/icon_16x16.png"), "its own guard")
        for own in (f"{catalog}/AppIcon.appiconset/Contents.json", f"{catalog}/Mark.imageset/mark.png",
                    "design/icon/AppIcon.appiconset/icon_16x16.png", "macos/Fixture/Resources/AppIcon.png"):
            self.assertIsNone(self.edit(own), own)

    def test_the_windows_images_are_exactly_what_assets_py_writes(self):
        self.app()
        assets = load(TOOLS / "windows" / "assets.py")
        with mock.patch.multiple(assets, read_png=mock.DEFAULT, trim=mock.DEFAULT, place=mock.DEFAULT,
                                 png_bytes=mock.DEFAULT, ico=mock.DEFAULT):
            written = [*assets.images(Path("icon.png")), "Dev/AppIcon.ico"]  # the Dev icon is `main`'s
        self.assertEqual(len(written), 22)
        for name in written:
            self.assertIn("`python3 laching/tools/windows/assets.py`", self.edit(f"{self.ASSETS}/{name}") or "", name)
            self.assertIsNone(self.read(f"{self.ASSETS}/{name}"), "edits only")
        # What else the app keeps in the folder is its own.
        for own in ("Hero.png", "Models/README.md", "Models/labels.txt", "Dev/Square44x44Logo.scale-100.png",
                    "Characters/big-cat-body.scale-200.png", "AppIcon.icon", "Square44x44Logo.svg"):
            self.assertIsNone(self.edit(f"{self.ASSETS}/{own}"), own)
        self.assertIsNone(self.edit("windows/Other.App/Assets/AppIcon.ico"), "another folder")
        self.app(lambda data: data["windows"].pop("assets"))
        self.assertIsNone(self.edit(f"{self.ASSETS}/AppIcon.ico"), "an app that doesn't run assets.py")

    def test_the_gradle_wrapper_s_scripts_where_the_app_has_an_android_folder(self):
        self.app()
        self.assertIsNone(self.edit("android/gradlew"), "no android section")
        self.app(lambda data: data.update(android={"dir": "android"}))
        self.assertIn("`./gradlew wrapper --gradle-version <version>` rewrites it", self.edit("android/gradlew"))
        self.assertTrue(self.edit("android/gradlew.bat"))
        self.assertIsNone(self.read("android/gradlew"), "edits only")
        for own in ("android/gradle/wrapper/gradle-wrapper.properties", "android/gradlew.md", "android/app/build.gradle.kts",
                    "android/settings.gradle.kts", "gradlew", "kotlin/gradlew"):
            self.assertIsNone(self.edit(own), own)

    def test_an_app_turns_a_baseline_guard_off_by_name(self):
        data = self.app(lambda data: data["hooks"].update(unguarded=["screenshots", "appIcon"]))
        for path in (self.SHOT, "screenshots/windows/home-light.png", self.ICON):
            self.assertIsNone(self.edit(path), path)
            self.assertIsNone(self.read(path), path)
        self.assertFalse(self.blocked("Read", file_path=str(self.root / self.SHOT)))
        self.assertTrue(self.edit(f"{self.ASSETS}/AppIcon.ico"), "the others stay on")
        # The schema knows the names, each of them and no other.
        self.assertEqual(companion.validate(data), [])
        schema = json.loads((TOOLS / "companion.schema.json").read_text("utf8"))
        names = schema["properties"]["hooks"]["properties"]["unguarded"]["items"]["enum"]
        self.assertEqual(names, list(self.hook.baseline("laching/tools")))
        data["hooks"]["unguarded"] = ["everything"]
        self.assertIn("hooks.unguarded", "\n".join(companion.validate(data)))
        data = self.app(lambda data: data["hooks"].update(unguarded=names))
        self.assertEqual(self.hook.baseline("laching/tools").keys() & {"screenshots"}, {"screenshots"})
        guards = [guard for guards in self.hook.baseline("laching/tools").values() for guard in guards]
        self.assertFalse([guard for guard in guards if guard in self.hook.GUARDED], "every one off")
        self.assertTrue(self.edit("conformance/phrases.json"), "the generators' guards aren't the baseline's to turn off")

    def test_the_app_s_own_guard_on_the_same_file_gives_its_reason(self):
        self.app(lambda data: data["hooks"].update(guards=[
            {"pattern": r"AppIcon\.appiconset/[^/]+\.png$", "why": "rendered by macos/scripts/icon.swift."},
            {"pattern": "^screenshots/", "why": "the store's pictures."}]))
        self.assertEqual(self.edit(self.ICON), f"{self.ICON}: rendered by macos/scripts/icon.swift.")
        self.assertEqual(self.edit(self.SHOT), f"{self.SHOT}: the store's pictures.")
        self.assertIn("never read or edit one", self.read(self.SHOT), "the app's guard is on edits; the baseline's on reads too")


class StopAndParity(HookTestCase):
    def setUp(self):
        super().setUp()
        os.environ["TMPDIR"] = self.tmp
        self.hook = load_hook()
        self.hook.setup(self.root)
        self.event = {"session_id": "parity"}

    def remind(self, last_edit):
        path = self.hook.state_file(self.event)
        lines = path.read_text().splitlines() if path.exists() else []
        last_ran = {v: float(t) for t, k, v in (line.split("\t", 2) for line in lines) if k == "ran"}
        return self.hook.parity_reminder(self.event, last_edit, last_ran)

    def test_mac_ui_edit_reminds_once(self):
        edits = {"macos/Fixture/Views/SettingsView.swift": 100.0}
        reminder = self.remind(edits) or ""
        self.assertIn("SettingsView.swift", reminder)
        self.assertIn("nothing under windows/Fixture.App", reminder)
        self.assertIsNone(self.remind(edits))

    def test_windows_edit_after_mac_is_enough(self):
        edits = {"macos/Fixture/Views/HomeView.swift": 100.0, "windows/Fixture.App/Views/HomeView.xaml": 120.0}
        self.assertIsNone(self.remind(edits))

    def test_not_ui(self):
        for path in ("macos/FixtureCore/Sources/FixtureCore/Phrases.swift", "macos/Fixture/App/AppModel.swift"):
            self.assertIsNone(self.remind({path: 100.0}), path)

    def test_slow_checks_come_from_companion_json(self):
        kinds = {kind: pattern for kind, pattern, _ in self.hook.SLOW}
        self.assertEqual(set(kinds), {"xcodebuild-test", "conformance", "swift-test", "dotnet-test", "dotnet-app-test",
                                      "winui-build"}, "no landing")
        self.assertRegex("windows/Fixture.App/Views/HomeView.xaml", kinds["winui-build"])
        self.assertRegex("windows/Fixture.Core/A.cs", kinds["winui-build"], "the app references the core")
        self.assertNotRegex("windows/Fixture.Core.Tests/A.cs", kinds["winui-build"])
        self.assertRegex("macos/FixtureCore/Sources/FixtureCore/A.swift", kinds["conformance"])
        self.assertRegex("windows/Fixture.Core/A.cs", kinds["dotnet-test"])
        self.assertNotRegex("windows/Fixture.App/A.cs", kinds["dotnet-test"])
        self.assertRegex("windows/Fixture.App/App/AppModel.cs", kinds["dotnet-app-test"])
        self.assertRegex("windows/Fixture.App.Tests/AppModelTests.cs", kinds["dotnet-app-test"])
        self.assertNotRegex("windows/Fixture.Core/A.cs", kinds["dotnet-app-test"])

    def enable_android(self, session: str = "android") -> None:
        """The fixture app with an Android port: android/ with core and app modules, and hooks.androidUI."""
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["android"] = {"dir": "android", "strings": "android/app/src/main/res"}
        data["hooks"]["androidUI"] = "^android/app/src/main/kotlin/app/fixture/ui/"
        config.write_text(json.dumps(data), "utf8")
        self.hook.setup(self.root)
        self.event = {"session_id": session}

    def test_android_port_is_named_once_configured(self):
        self.enable_android()
        edits = {"macos/Fixture/Views/SettingsView.swift": 100.0}
        reminder = self.remind(edits) or ""
        self.assertIn("nothing under windows/Fixture.App or android/app since", reminder)
        self.assertIn("Windows and Android ports in the same change (windows-port skill)", reminder)
        self.assertIn("no Windows and Android counterpart", reminder)
        self.assertIsNone(self.remind(edits), "once")
        # The Windows port is in; only Android is missing.
        self.event = {"session_id": "android2"}
        edits = {"macos/Fixture/Views/HomeView.swift": 100.0, "windows/Fixture.App/Views/HomeView.xaml": 120.0}
        reminder = self.remind(edits) or ""
        self.assertIn("nothing under android/app since", reminder)
        self.assertIn("the Android port in the same change.", reminder)
        self.assertNotIn("windows-port", reminder)
        edits["android/app/src/main/kotlin/app/fixture/ui/HomeScreen.kt"] = 130.0
        self.event = {"session_id": "android3"}
        self.assertIsNone(self.remind(edits), "both ports are in")

    def test_android_slow_checks_and_gradle_runs(self):
        self.enable_android()
        kinds = {kind: pattern for kind, pattern, _ in self.hook.SLOW}
        self.assertIn("gradle-core-test", kinds)
        self.assertIn("android-build", kinds)
        self.assertRegex("android/core/src/main/kotlin/A.kt", kinds["gradle-core-test"])
        self.assertRegex("conformance/scheduler/x.json", kinds["gradle-core-test"])
        self.assertNotRegex("android/app/src/main/kotlin/A.kt", kinds["gradle-core-test"])
        self.assertRegex("android/app/src/main/kotlin/A.kt", kinds["android-build"])
        self.assertNotRegex("android/core/src/main/kotlin/A.kt", kinds["android-build"])
        self.assertIn("./gradlew :core:test", dict((k, label) for k, _, label in self.hook.SLOW)["gradle-core-test"])
        sessions = iter(range(100))

        def reminded_after(path, command, label):
            session = f"gradle{next(sessions)}"
            self.run_hook("post", {"session_id": session, "tool_name": "Edit",
                                   "tool_input": {"file_path": str(self.root / path)}})
            if command:
                self.run_hook("post", {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}})
            return label in self.run_hook("stop", {"session_id": session}).stderr

        core, app = "android/core/src/main/kotlin/laching/A.kt", "android/app/src/main/kotlin/app/fixture/A.kt"
        self.assertTrue(reminded_after(core, None, "Kotlin core tests"))
        self.assertFalse(reminded_after(core, "cd android && ./gradlew :core:test", "Kotlin core tests"))
        self.assertFalse(reminded_after(core, "cd android && ./gradlew test", "Kotlin core tests"), "every module")
        self.assertTrue(reminded_after(core, "cd android && ./gradlew :app:test", "Kotlin core tests"), "another module")
        # Only verify.sh knows what it ran (its note in the Git folder): a command that names it runs nothing.
        for naming in ("laching/tools/verify.sh", "laching/tools/verify.sh --help", "grep -n slow laching/tools/verify.sh"):
            self.assertTrue(reminded_after(core, naming, "Kotlin core tests"), naming)
        self.assertTrue(reminded_after(app, None, "Android app build"))
        self.assertFalse(reminded_after(app, "cd android && ./gradlew :app:assembleDebug", "Android app build"))
        self.assertFalse(reminded_after(app, "cd android && ./gradlew build", "Android app build"))
        self.assertTrue(reminded_after(app, "cd android && ./gradlew :core:test", "Android app build"))
        self.assertTrue(reminded_after(app, "laching/tools/verify.sh", "Android app build"), "naming it builds no APK")

    @unittest.skipIf(os.name == "nt", "the fake ktlint is a shell script")
    def test_kotlin_files_are_formatted_with_ktlint_when_it_is_installed(self):
        self.enable_android()
        bin_dir = Path(self.tmp) / "bin"
        bin_dir.mkdir()
        log = Path(self.tmp) / "ktlint.log"
        ktlint = bin_dir / "ktlint"
        ktlint.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n', "utf8")
        ktlint.chmod(0o755)
        for path in ("android/app/src/main/kotlin/app/fixture/A.kt", "android/build.gradle.kts", "kotlin/core/B.kt",
                     "macos/Fixture/C.kt", "android/README.md"):
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text("x\n", "utf8")
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            for path in ("android/app/src/main/kotlin/app/fixture/A.kt", "android/build.gradle.kts", "kotlin/core/B.kt",
                         "macos/Fixture/C.kt", "android/README.md"):
                self.hook.format_file(path)
        self.assertEqual(log.read_text().splitlines(),
                         ["-F android/app/src/main/kotlin/app/fixture/A.kt", "-F android/build.gradle.kts", "-F kotlin/core/B.kt"])
        log.unlink()
        with mock.patch.dict(os.environ, {"PATH": str(Path(self.tmp) / "empty")}):
            self.hook.format_file("android/build.gradle.kts")
        self.assertFalse(log.exists(), "no ktlint, no formatting")

    @unittest.skipIf(os.name == "nt", "the fake dotnet is a shell script")
    def test_csharp_files_are_formatted_from_the_windows_folder_so_its_global_json_picks_the_sdk(self):
        bin_dir = Path(self.tmp) / "bin"
        bin_dir.mkdir()
        log = Path(self.tmp) / "dotnet.log"
        dotnet = bin_dir / "dotnet"
        dotnet.write_text(f'#!/bin/sh\necho "$(pwd -P) $*" >> "{log}"\n', "utf8")
        dotnet.chmod(0o755)
        for path in ("windows/Fixture.Core/A.cs", "macos/Fixture/B.cs"):
            (self.root / path).write_text("class A {}\n", "utf8")
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            for path in ("windows/Fixture.Core/A.cs", "windows/Fixture.Core/Fixture.Core.csproj", "macos/Fixture/B.cs"):
                self.hook.format_file(path)
        self.assertEqual(log.read_text().splitlines(),
                         [f"{self.root / 'windows'} format whitespace . --folder --include Fixture.Core/A.cs"])

    def test_app_model_tests_count_when_their_project_or_the_solution_ran(self):
        edit = {"session_id": "app", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "windows/Fixture.App/App/AppModel.cs")}}

        def reminded_after(command):
            self.run_hook("post", edit)
            self.run_hook("post", {"session_id": "app", "tool_name": "Bash", "tool_input": {"command": command}})
            return "Windows app model tests" in self.run_hook("stop", {"session_id": "app"}).stderr

        # The reminder runs them from the Windows folder, so the SDK is the one its global.json pins.
        label = dict((kind, label) for kind, _, label in self.hook.SLOW)["dotnet-app-test"]
        self.assertEqual(label, "Windows app model tests (cd windows && dotnet test Fixture.App.Tests)")
        self.assertTrue(reminded_after("dotnet test windows/Fixture.Core.Tests"), "another test project")
        self.assertTrue(reminded_after("cd windows && dotnet test Fixture.Core.Tests"), "another test project")
        self.assertFalse(reminded_after("dotnet test windows/Fixture.App.Tests"))
        self.assertFalse(reminded_after("cd windows && dotnet test Fixture.App.Tests"), "the reminder's form")
        self.assertFalse(reminded_after("cd windows && dotnet test"), "the solution")

    def test_the_winui_build_counts_from_the_vm_or_dotnet(self):
        sessions = iter(range(100))

        def reminded_after(command):
            session = f"winui{next(sessions)}"  # the log has whole seconds: one session per command
            self.run_hook("post", {"session_id": session, "tool_name": "Edit",
                                   "tool_input": {"file_path": str(self.root / "windows/Fixture.App/Views/HomeView.xaml")}})
            if command:
                self.run_hook("post", {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}})
            return "WinUI app build" in self.run_hook("stop", {"session_id": session}).stderr

        self.assertTrue(reminded_after(None))
        self.assertIn("vm.sh build", self.hook.SLOW[-1][2])
        for command in ("laching/tools/windows/vm.sh build", "laching/tools/windows/vm.sh build ARM64 -- -c Release",
                        "laching/tools/windows/vm.sh screenshots /tmp/shots -- -Language fr",
                        "laching/tools/windows/vm.sh smoke", "dotnet build windows/Fixture.App -p:Platform=x64",
                        "cd windows && dotnet build Fixture.App -p:Platform=x64",
                        "dotnet publish windows/Fixture.App/Fixture.App.csproj -c Release"):
            self.assertFalse(reminded_after(command), command)
        for command in ("laching/tools/windows/vm.sh status", "laching/tools/windows/vm.sh sync",
                        "dotnet build windows/Fixture.Core", "cd windows && dotnet build Fixture.Core"):
            self.assertTrue(reminded_after(command), command)

    def test_vm_tests_count_as_the_dotnet_tests(self):
        edit = {"session_id": "vmtest", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "windows/Fixture.Core/A.cs")}}
        self.run_hook("post", edit)
        self.assertIn("Windows Core tests", self.run_hook("stop", {"session_id": "vmtest"}).stderr)
        self.run_hook("post", {"session_id": "vmtest", "tool_name": "Bash",
                               "tool_input": {"command": "laching/tools/windows/vm.sh test"}})
        self.assertNotIn("Windows Core tests", self.run_hook("stop", {"session_id": "vmtest"}).stderr)

    def test_app_slow_checks_replace_and_add(self):
        self.configure(slow=[
            {"name": "conformance", "paths": "^macos/Fixture/Model/", "label": "the app's vectors"},
            {"name": "app-build", "paths": "^windows/Fixture\\.App/", "label": "the tray app build",
             "ran": "\\bdotnet\\s+build\\b.*Fixture\\.App"},
        ])
        self.hook.setup(self.root)
        checks = {kind: (pattern, label) for kind, pattern, label in self.hook.SLOW}
        self.assertEqual(checks["conformance"], ("^macos/Fixture/Model/", "the app's vectors"))
        self.assertEqual(checks["app-build"][1], "the tray app build")
        self.assertIn("swift-test", checks, "the other built-in checks stay")
        edit = {"session_id": "slow", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "windows/Fixture.App/Tray.cs")}}
        self.run_hook("post", edit)
        done = self.run_hook("stop", {"session_id": "slow"})
        self.assertIn("the tray app build", done.stderr)
        self.run_hook("post", {"session_id": "slow", "tool_name": "Bash",
                               "tool_input": {"command": "dotnet build windows/Fixture.App/Fixture.App.csproj"}})
        self.assertNotIn("the tray app build", self.run_hook("stop", {"session_id": "slow"}).stderr)

    def test_cloud_stop_does_nothing_outside_github_actions(self):
        env_actions = os.environ.pop("GITHUB_ACTIONS", None)
        try:
            done = self.run_hook("cloud-stop", {"session_id": "cloud"})
        finally:
            if env_actions is not None:
                os.environ["GITHUB_ACTIONS"] = env_actions
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "{}"))

    def test_stop_runs_the_fast_checks_and_names_the_slow_ones(self):
        # A conformance test, which the vectors cover and the strings check doesn't (the fixture's German has gaps).
        conformance = "macos/FixtureCore/Tests/FixtureCoreTests/Conformance/A.swift"
        event = {"session_id": "stop", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / conformance)}}
        self.assertEqual(self.run_hook("post", event).returncode, 0)
        done = self.run_hook("stop", {"session_id": "stop"})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("Not run since your last edit:\n- conformance vectors (", done.stderr)
        # Laching's own (the fixture has no scripts/ wrapper), wherever this checkout of it is.
        self.assertIn(f"tools/verify.sh -- {conformance}` (it runs only what applies)", done.stderr)
        # A command that names verify.sh (a --help, a cat, a run that failed) isn't a slow check that ran.
        self.run_hook("post", {"session_id": "stop", "tool_name": "Bash", "tool_input": {"command": "scripts/verify.sh"}})
        again = self.run_hook("stop", {"session_id": "stop"})
        self.assertEqual(again.returncode, 2, again.stderr)
        self.assertIn("Not run since your last edit:\n- conformance vectors (", again.stderr)

    @unittest.skipIf(BASH is None, NO_SHELL)
    def test_the_reminder_names_for_each_slow_check_only_the_files_that_ask_for_it(self):
        """With every file of the session, the command would start the slow checks of all of them."""
        fake_verify(self.root, "exit 0\n")
        log = self.hook.state_file(self.event)
        core, swift = "windows/Fixture.Core/A.cs", "macos/FixtureCore/Sources/FixtureCore/A.swift"

        def reminder(*lines):
            log.write_text("".join(f"{line}\n" for line in lines), "utf8")
            return self.hook.stop_problems(self.event)[-1].splitlines()

        said = reminder(f"100.0\tedit\t{core}", "101.0\tedit\tdocs/notes.md")
        self.assertEqual(said[0], "Not run since your last edit:")
        self.assertEqual(len(said), 3, "the checks one file asks for share a line")
        self.assertRegex(said[1], rf"^- Windows Core tests, WinUI app build \(.+\), for {core}$")
        self.assertEqual(said[2], f"Run `scripts/verify.sh -- {core}` (it runs only what applies), "
                                  "or tell the user why you're skipping it.")
        # Each check with the files it waits on, and one command for them all. A file the check ran after is none of
        # them, and neither is one no check is for.
        later = "windows/Fixture.Core/B.cs"
        said = reminder(f"100.0\tedit\t{core}", "101.0\tedit\tdocs/notes.md", "102.0\tran\tdotnet-test",
                        f"103.0\tedit\t{later}", f"104.0\tedit\t{swift}")
        windows = {line.split(", ")[0]: line.split(", for ")[-1] for line in said[1:-1] if "Windows" in line}
        self.assertEqual(windows, {"- Windows Core tests": later, "- WinUI app build (laching/tools/windows/vm.sh build "
                                   "in the Parallels VM": f"{core} {later}"})
        self.assertEqual([line.split(", for ")[-1] for line in said[1:-1] if "core package tests" in line], [swift])
        self.assertEqual(said[-1], f"Run `scripts/verify.sh -- {swift} {core} {later}` (it runs only what applies), "
                                   "or tell the user why you're skipping it.")
        self.assertNotIn("notes.md", "\n".join(said))

    def test_a_slow_check_counts_once_verify_noted_that_it_passed(self):
        """verify.sh notes each slow check that passed in the Git folder (`SLOW_LOG`): when it started, the hook's kinds,
        its command. The hook reads that at every stop."""
        git(self.root, "init", "-q")
        log = self.root / ".git" / self.hook.SLOW_LOG
        session = {"session_id": "noted"}
        conformance = "macos/FixtureCore/Tests/FixtureCoreTests/Conformance/A.swift"
        self.run_hook("post", {**session, "tool_name": "Edit", "tool_input": {"file_path": str(self.root / conformance)}})
        now = int(time.time())

        def stop_after(line):
            with log.open("a", encoding="utf8", newline="\n") as f:
                f.write(line + "\n")
            return self.run_hook("stop", session)

        done = stop_after(f"{now - 3600}\tconformance swift-test\tan hour before the edit")
        self.assertIn("conformance vectors", done.stderr)
        self.assertIn("core package tests", done.stderr)
        done = stop_after(f"soon\tconformance swift-test\tno time\nnot a line\n{now}\tconformance")
        self.assertIn("conformance vectors", done.stderr, "a line that isn't one is passed over")
        done = stop_after(f"{now}\tconformance\tlaching/tools/conformance.sh --check")
        self.assertEqual(done.returncode, 2)
        self.assertNotIn("conformance vectors", done.stderr)
        self.assertIn("Not run since your last edit:\n- core package tests (", done.stderr, "only the kinds it names")
        # A check the hook has no name for (the app's own) is known by its command, as one the agent ran is.
        done = stop_after(f"{now}\t\tbash -c swift test --package-path macos/FixtureCore")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(set(self.hook.verify_ran()), {"conformance", "swift-test"})
        # Another edit, and they are stale again.
        time.sleep(1.1)  # the note's stamps are whole seconds
        self.run_hook("post", {**session, "tool_name": "Edit", "tool_input": {"file_path": str(self.root / conformance)}})
        self.assertIn("conformance vectors", self.run_hook("stop", session).stderr)

    def test_every_built_in_slow_check_is_one_companion_knows(self):
        """`companion.py consistency` asks for `ran` on a hooks.slow entry that isn't one of these, and `derived` gives
        verify.sh the paths an app sets for one that is: the hook's own checks are companion.py's, each with a label."""
        self.enable_android()
        self.assertLessEqual({kind for kind, _, _ in self.hook.SLOW}, set(companion.SLOW_CHECKS))
        source = HOOK.read_text("utf8")
        for kind in companion.SLOW_CHECKS:
            self.assertIn(f'"{kind}":', source, "a slow check hook.py's setup has a label for")

    @unittest.skipIf(BASH is None, NO_SHELL)
    def test_stop_reports_what_the_fast_checks_reject(self):
        fake_verify(self.root, 'echo "lint: rejected $*"\nexit 1\n')
        edited = "macos/FixtureCore/Sources/FixtureCore/A.swift"
        event = {"session_id": "rejected", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / edited)}}
        self.assertEqual(self.run_hook("post", event).returncode, 0)
        done = self.run_hook("stop", {"session_id": "rejected"})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn(f"Fast checks on the files you edited failed:\nlint: rejected --fast -- {edited}", done.stderr)

    @unittest.skipIf(BASH is None, NO_SHELL)
    def test_outside_a_repository_every_stop_runs_the_fast_checks(self):
        """The hook can't see there what changed since a pass (`FastChecksMemo`), so it remembers none."""
        if self.hook.tree_state(self.root) is not None:
            self.skipTest("the temporary folder is inside a repository")
        runs = Path(self.tmp) / "runs"
        fake_verify(self.root, f"echo run >> {shlex.quote(runs.as_posix())}\n")
        self.hook.record(self.event, "edit", "notes.md")
        for _ in range(2):
            self.assertEqual(self.hook.stop_problems(self.event), [])
        self.assertEqual(len(runs.read_text("utf8").splitlines()), 2)
        self.assertNotIn("\tpassed\t", self.hook.state_file(self.event).read_text("utf8"))

    def test_stop_says_so_when_windows_has_no_bash(self):
        self.hook.record(self.event, "edit", "macos/FixtureCore/Sources/FixtureCore/A.swift")
        stderr = io.StringIO()
        with mock.patch.object(self.hook.companion, "shell_command", return_value=None), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                self.hook.stop(self.event)
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("didn't run: Windows starts no shell script itself and there's no Git for Windows bash here",
                      stderr.getvalue())


@unittest.skipIf(BASH is None, NO_SHELL)
class FastChecksMemo(HookTestCase):
    """`stop` over a state that already passed the fast checks runs them no second time: most stops come with no edit
    after the last one. The app is a repository here, with a wrapper that counts its runs and fails while notes.md or
    aside.txt holds `bad`; Laching is a second one, so work in this checkout during a test is no new state."""

    def setUp(self):
        super().setUp()
        self.runs = Path(self.tmp) / "runs"
        fake_verify(self.root, f"echo run >> {shlex.quote(self.runs.as_posix())}\n! grep -qs bad notes.md aside.txt\n")
        (self.root / ".gitignore").write_text("ignored/\n", "utf8")
        self.commit(self.root)
        self.laching = Path(self.tmp) / "laching"
        (self.laching / "tools").mkdir(parents=True)
        (self.laching / "tools" / "verify.sh").write_text("#!/bin/sh\n", "utf8")
        self.commit(self.laching)
        self.hook = load_hook()
        for patch in (mock.patch.dict(os.environ, {"TMPDIR": self.tmp}),
                      mock.patch.object(self.hook, "TOOLS", self.laching / "tools")):
            patch.start()
            self.addCleanup(patch.stop)
        self.hook.setup(self.root)
        self.event = {"session_id": "memo"}

    @staticmethod
    def commit(folder):
        if not (folder / ".git").exists():
            git(folder, "init", "-q")
        git(folder, "add", "-A")
        git(folder, "commit", "-qm", "a commit")

    def edit(self, path, text, event=None):
        """An edit of the session's: the file as `text`, and the line the post hook leaves for it."""
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text, "utf8")
        self.hook.record(event or self.event, "edit", path)

    def count(self):
        return len(self.runs.read_text("utf8").splitlines()) if self.runs.exists() else 0

    def stop(self, event=None):
        """A stop: how many times the fast checks ran for it, and whether it reports that they failed."""
        before = self.count()
        problems = self.hook.stop_problems(event or self.event)
        return self.count() - before, any("Fast checks on the files you edited failed" in one for one in problems)

    def test_a_second_stop_with_no_edit_in_between_runs_no_check(self):
        self.edit("notes.md", "fine\n")
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (0, False), "this state passed a moment ago")
        self.assertEqual(self.stop(), (0, False))
        self.edit("notes.md", "fine, and longer\n")
        self.assertEqual(self.stop(), (1, False), "an edit is a new state")
        self.assertEqual(self.stop(), (0, False))
        self.edit("docs/more.md", "another file\n")
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (0, False))

    def test_the_hook_as_an_agent_runs_it_stops_twice_and_checks_once(self):
        """The same through `hook.py stop`, with this checkout's tools as Laching's."""
        before = self.hook.tree_state(TOOLS)
        (self.root / "notes.md").write_text("fine\n", "utf8")
        edit = {"session_id": "twice", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / "notes.md")}}
        self.assertEqual(self.run_hook("post", edit).returncode, 0)
        for _ in range(2):
            done = self.run_hook("stop", {"session_id": "twice"})
            self.assertEqual((done.returncode, done.stderr), (0, ""))
        if before is None or self.hook.tree_state(TOOLS) != before:
            self.skipTest("this checkout is no repository, or changed during the test: a new state")
        self.assertEqual(self.count(), 1)

    def test_a_failure_is_never_remembered(self):
        self.edit("notes.md", "bad\n")
        self.assertEqual(self.stop(), (1, True))
        self.assertEqual(self.stop(), (1, True), "a state that fails is checked at every stop")
        self.assertNotIn("\tpassed\t", self.hook.state_file(self.event).read_text("utf8"))
        self.edit("notes.md", "fine now\n")
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (0, False))

    def test_a_pass_never_hides_what_a_command_broke_since(self):
        """No line of the session's log says what its commands did to the tree, and the checks read far more than the
        edited files."""
        self.edit("notes.md", "fine\n")
        self.assertEqual(self.stop(), (1, False))
        (self.root / "aside.txt").write_text("bad\n", "utf8")
        self.assertEqual(self.stop(), (1, True))
        self.assertEqual(self.stop(), (1, True))

    def test_whatever_git_sees_change_is_a_new_state(self):
        self.edit("notes.md", "fine\n")
        self.assertEqual(self.stop(), (1, False))
        listing, aside = self.root / "store-listing" / "en.md", self.root / "aside.txt"
        for change, what in (
            (lambda: listing.write_text("rewritten\n", "utf8"), "a tracked file a command rewrote"),
            (lambda: listing.write_text("rewritten once more\n", "utf8"), "the same file again, changed already"),
            (lambda: git(self.root, "add", "-A"), "staged"),
            (lambda: self.commit(self.root), "committed"),
            (lambda: aside.write_text("new\n", "utf8"), "a new file"),
            (lambda: aside.write_text("new, and rewritten\n", "utf8"), "the new file again"),
            (aside.unlink, "the new file gone"),
            (lambda: git(self.root, "checkout", "-q", "HEAD~1"), "another commit checked out"),
        ):
            change()
            self.assertEqual(self.stop(), (1, False), what)
            self.assertEqual(self.stop(), (0, False), what)

    def test_a_file_git_ignores_counts_once_the_session_edited_it(self):
        ignored = self.root / "ignored" / "notes.md"
        self.edit("ignored/notes.md", "fine\n")
        self.assertNotIn(b"notes.md", self.hook.tree_state(self.root), "Git doesn't see it")
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (0, False))
        ignored.write_text("rewritten by a command\n", "utf8")
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (0, False))
        ignored.unlink()
        self.assertEqual(self.stop(), (1, False), "a deleted file is a new state")
        self.assertEqual(self.stop(), (0, False))

    def test_laching_s_tools_are_part_of_the_state(self):
        self.edit("notes.md", "fine\n")
        self.assertEqual(self.stop(), (1, False))
        (self.laching / "tools" / "verify.sh").write_text("#!/bin/sh\n# changed\n", "utf8")
        self.assertEqual(self.stop(), (1, False), "the checks themselves changed")
        self.assertEqual(self.stop(), (0, False))
        self.commit(self.laching)
        self.assertEqual(self.stop(), (1, False), "another commit of Laching")
        self.assertEqual(self.stop(), (0, False))

    def test_a_git_too_slow_for_the_stop_leaves_nothing_remembered(self):
        """The stop has a few seconds beside the checks' own timeout, and the key gets no more of them."""
        asked, git_itself = [], self.hook.companion.git

        def slow(folder, *args, timeout=None, **more):
            if timeout is None:
                return git_itself(folder, *args, **more)
            if "--show-toplevel" in args:
                asked.append(timeout)
            raise subprocess.TimeoutExpired("git", timeout)

        self.edit("notes.md", "fine\n")
        with mock.patch.object(self.hook.companion, "git", slow):
            self.assertEqual(self.stop(), (1, False))
            self.assertEqual(self.stop(), (1, False))
        self.assertEqual(len(asked), 2)
        self.assertTrue(all(0 < timeout <= 8 for timeout in asked), asked)
        self.assertEqual(self.stop(), (1, False), "nothing was remembered meanwhile")
        self.assertEqual(self.stop(), (0, False))

    def test_a_run_that_changes_the_tree_is_followed_by_one_more(self):
        """The key is read before the run, so a pass stands for the state the checks started from: one that rewrote a
        file (a regenerated project, say) left another state, which the next stop checks."""
        fake_verify(self.root, f"echo run >> {shlex.quote(self.runs.as_posix())}\n"
                               "[ -f regenerated.txt ] || echo regenerated > regenerated.txt\n")
        self.edit("notes.md", "fine\n")
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (1, False))
        self.assertEqual(self.stop(), (0, False))

    def test_the_memo_is_each_session_s_own(self):
        other = {"session_id": "other"}
        self.edit("notes.md", "fine\n")
        self.assertEqual(self.stop(), (1, False))
        self.hook.record(other, "edit", "notes.md")
        self.assertEqual(self.stop(other), (1, False), "another session checks its own edits")
        self.assertEqual(self.stop(other), (0, False))
        self.assertEqual(self.stop(), (0, False))
        log = self.hook.state_file(self.event)  # next to the session's edits and runs, which read it as neither
        self.assertEqual([line.split("\t")[1] for line in log.read_text("utf8").splitlines()], ["edit", "passed"])
        self.assertNotIn("passed", self.run_hook("resume", self.event).stdout)

    def test_the_key_holds_the_edited_files_the_script_companion_json_and_the_link(self):
        """Each on its own, whatever Git sees of it: the script and companion.json may be ignored there."""
        notes, script, seen = self.root / "notes.md", self.hook.verify_script(), []
        notes.write_text("fine\n", "utf8")
        link = self.root / "windows" / "Directory.Build.targets"  # as `companion.py link` marks it
        with mock.patch.object(self.hook, "tree_state", return_value=b""):
            seen.append(self.hook.fast_state(script, ["notes.md"]))
            self.assertEqual(self.hook.fast_state(script, ["notes.md"]), seen[0], "the same state, the same key")
            for change in (
                lambda: notes.write_text("fine, rewritten\n", "utf8"),
                notes.unlink,
                lambda: script.write_text(script.read_text("utf8") + "# changed\n", "utf8", newline="\n"),
                lambda: self.configure(bigFileLines=700),
                lambda: link.write_text(f"<Project>\n  <!-- {companion.LINK_MARKER}: a trial -->\n</Project>\n", "utf8"),
            ):
                change()
                self.assertNotIn(self.hook.fast_state(script, ["notes.md"]), seen)
                seen.append(self.hook.fast_state(script, ["notes.md"]))
            self.assertNotIn(self.hook.fast_state(script, ["more.md", "notes.md"]), seen, "another file of the session's")
        with mock.patch.object(self.hook, "tree_state", return_value=None):
            self.assertIsNone(self.hook.fast_state(script, ["notes.md"]), "nothing to remember a pass under")

    def test_the_tree_state_reads_renames_and_into_submodules(self):
        clean = self.hook.tree_state(self.root)
        self.assertRegex(clean, rb"^# branch\.oid [0-9a-f]{40,64}$")
        self.assertEqual(self.hook.tree_state(self.root / "store-listing"), clean, "from any folder of the repository")
        git(self.root, "mv", "store-listing/en.md", "store-listing/english.md")
        renamed = self.hook.tree_state(self.root).split(b"\0")
        self.assertEqual(len(renamed), 4, "the commit, the entry, the file's stamp, the path it had")
        self.assertTrue(renamed[1].endswith(b" store-listing/english.md"), renamed[1])
        self.assertEqual(renamed[3], b"store-listing/en.md")
        self.assertNotEqual(renamed[2], b"gone")
        vendored = Path(self.tmp) / "vendored"
        vendored.mkdir()
        (vendored / "a.txt").write_text("a\n", "utf8")
        self.commit(vendored)
        git(self.root, "submodule", "add", "-q", vendored.as_uri(), "vendored")
        self.commit(self.root)
        clean = self.hook.tree_state(self.root)
        (self.root / "vendored" / "a.txt").write_text("changed\n", "utf8")
        changed = self.hook.tree_state(self.root)
        self.assertNotEqual(changed, clean)
        (self.root / "vendored" / "a.txt").write_text("changed once more\n", "utf8")
        self.assertNotEqual(self.hook.tree_state(self.root), changed, "Git says the submodule has changes; which is read in it")


class WhatACommandRan(HookTestCase):
    """Which slow checks a shell command counts for (`ran_kinds`): the ones it runs, where a failure would have failed
    the command. The hook hears of a command that succeeded, so a check the command only names, or one whose exit
    status a pipe, a `;` or a `||` replaced, says nothing about the tests."""

    def setUp(self):
        super().setUp()
        self.hook = load_hook()
        self.hook.setup(self.root)

    def enable_android(self):
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["android"] = {"dir": "android", "strings": "android/app/src/main/res"}
        config.write_text(json.dumps(data), "utf8")
        self.hook.setup(self.root)

    def assert_ran(self, command, *kinds):
        self.assertEqual(self.hook.ran_kinds(command), list(kinds), command)

    def test_a_command_that_names_a_check_runs_nothing(self):
        self.enable_android()
        for command in (
            'grep -rn "swift test" docs/',
            "grep -rn swift test docs/",
            'echo "then run dotnet test"',
            "echo then run dotnet test",
            "cat > notes.md <<'EOF'\nrun laching/tools/conformance.sh\n./gradlew :app:assembleDebug\nswift test\nEOF",
            "cat <<-EOF > notes.md\n\tdotnet test\n\tEOF",
            "cat << EOF >> notes.md\nswift test\nEOF\ngit status",
            'git commit -m "conformance.sh: the vectors are regenerated"',
            "git commit -m \"$(cat <<'EOF'\nA title\n\nswift test passes; ./gradlew build too\nEOF\n)\"",
            "git log --grep 'dotnet test'",
            "cat laching/tools/conformance.sh",
            "ls macos # then swift test",
            "echo 'a; swift test'",  # a quoted separator
            'echo "a && dotnet test"',
            'echo "a\nswift test\nb"',
            "which swift test xcodebuild",
            "man xcodebuild test",
            "swift build && echo swift test",
            "cat <<EOF\nfoo \\\nEOF\nswift test\nEOF",  # an unquoted one's lines join first: the second EOF ends it
        ):
            self.assert_ran(command)

    def test_a_check_whose_exit_status_is_lost_ran_nothing(self):
        for command in (
            "dotnet test | tail",
            "swift test 2>&1 | tail -20",
            "swift test |& tee log.txt",
            "swift test; echo done",
            "swift test\necho done",
            "swift test || true",
            "swift test && echo ok || echo failed",
            "swift test &",
            "if swift test; then echo ok; fi",
            "! swift test",
            "for package in a b; do swift test --package-path $package; done",
            "echo $(swift test)",  # a command substitution
            "out=`swift test`",
            'echo "$(swift test 2>&1)" > log.txt',
            "diff <(swift test) expected.txt",
            "(swift test) | tail",  # a subshell
            "(swift test; true)",
            "bash -c 'swift test | tail'",
            "swift test || exit 0",
            "swift test || { echo failed; exit; }; echo done",  # `exit` with echo's status
            "for s in A B; do swift test || exit 1; done | tee log",  # the loop's exit ends a pipe's subshell
            "{ swift test; } | tail",
            "{ swift test; }; echo done",
            "set -e; swift test && echo ok; echo done",  # `set -e` skips all of an `&&` list but its last
            "set -e; (swift test; echo ok) || true",
            "set -e; set +e; swift test; echo done",
            "set -e; while swift test; do sleep 1; done; echo done",
            "if true; then swift test; fi",  # an `if`'s body: left out, its rules aren't worth the parsing
            "echo 'a \\\n'; swift test; echo done",
        ):
            self.assert_ran(command)
        for command in (
            "swift test",
            "swift test;",
            "swift test\n",
            "swift test && echo ok",
            "swift build && swift test",
            "swift test > out.txt 2>&1 && tail out.txt",
            "git status; swift test",
            "(swift test)",
            "(cd macos && swift test --package-path FixtureCore)",
            "set -o pipefail; swift test 2>&1 | tail -20",
            "set -euo pipefail\nswift test | xcbeautify",
            "swift test # | tail",
            # A failure that ends the shell is its exit status, whatever follows.
            "swift test || exit 1",
            "swift test || exit $?",
            "swift test || { echo failed; exit 1; }; echo done",
            "{ swift test; } 2>&1",
            "{ swift test; } > log.txt 2>&1",
            "set -e; swift test; echo done",
            "set -eu\nswift test\necho done",
            "set -o errexit; swift test; echo done",
            "set -e; { swift test; echo done; }",
            "set -e; if true; then swift test; fi; echo done",
            "set -e; (swift test; echo ok); echo done",
            "set -e; true && swift test; echo done",
            "cat > notes.sh <<'EOF'\nfoo \\\nEOF\nswift test",  # a quoted heredoc's body joins no lines
            "# a comment \\\nswift test",  # a comment ends at the newline
        ):
            self.assert_ran(command, "swift-test")
        for command in (
            "for s in A B; do xcodebuild -scheme $s test || exit 1; done",
            "set -e\nfor s in A B; do xcodebuild -scheme $s test; done\necho done",
        ):
            self.assert_ran(command, "xcodebuild-test")

    def test_a_check_counts_where_the_command_runs_it(self):
        self.enable_android()
        self.assert_ran("cd macos && swift test --package-path FixtureCore", "swift-test")
        self.assert_ran("swift test \\\n  --package-path macos/FixtureCore \\\n  --parallel", "swift-test")
        self.assert_ran("env X=1 swift test", "swift-test")
        self.assert_ran("X=1 Y='a b' swift test", "swift-test")
        self.assert_ran("xcrun swift test", "swift-test")
        self.assert_ran("timeout 600 /usr/bin/swift test", "swift-test")
        self.assert_ran("bash -lc 'cd macos && swift test --package-path FixtureCore'", "swift-test")
        self.assert_ran("time dotnet test", "dotnet-test", "dotnet-app-test")
        self.assert_ran("cd windows && time dotnet test Fixture.Core.Tests", "dotnet-test")
        self.assert_ran("./gradlew :core:test", "gradle-core-test")
        self.assert_ran("cd android && ./gradlew :core:test :app:assembleDebug", "gradle-core-test", "android-build")
        self.assert_ran("JAVA_HOME=/jdk ./gradlew -q --build-cache :app:assembleDebug", "android-build")
        self.assert_ran("laching/tools/conformance.sh", "conformance")
        self.assert_ran("bash laching/tools/conformance.sh --check", "conformance")
        xcodebuild = "xcodebuild -project Fixture.xcodeproj -scheme Fixture -destination 'platform=macOS' test"
        self.assert_ran(f"cd macos && {xcodebuild}", "xcodebuild-test")
        # Some of the tests ran: the check ran, but it doesn't count (`UNJUDGED`); a listing ran none.
        unjudged = self.hook.UNJUDGED
        self.assert_ran(f"{xcodebuild} -only-testing:FixtureTests/A", unjudged + "xcodebuild-test")
        self.assert_ran(f"{xcodebuild} -skip-testing:FixtureTests/A", unjudged + "xcodebuild-test")
        self.assert_ran("swift test --filter PhraseTests", unjudged + "swift-test")
        self.assert_ran("swift test --package-path macos/FixtureCore --skip SlowTests", unjudged + "swift-test")
        self.assert_ran("swift test --skip-build", "swift-test")
        self.assert_ran("swift test list")
        self.assert_ran("swift test --list-tests")
        self.assert_ran("dotnet test --filter Name~Phrase", unjudged + "dotnet-test", unjudged + "dotnet-app-test")
        self.assert_ran("dotnet test --list-tests")
        self.assert_ran("./gradlew :core:test --tests '*PhraseTest'", unjudged + "gradle-core-test")
        self.assert_ran("./gradlew build -x test", "android-build")
        # A test project runs its own tests only.
        self.assert_ran("dotnet test windows/Fixture.App.Tests", "dotnet-app-test")
        self.assert_ran("cd windows && dotnet test Fixture.Core.Tests/Fixture.Core.Tests.csproj", "dotnet-test")
        self.assert_ran("cd windows/Fixture.App.Tests && dotnet test", "dotnet-app-test")
        self.assert_ran("dotnet test windows/Fixture.Other.Tests")
        self.assert_ran("xcodebuild -scheme Fixture build-for-testing")
        self.assert_ran("laching/tools/windows/vm.sh build && laching/tools/windows/vm.sh test",
                        "winui-build", "dotnet-test", "dotnet-app-test")

    def test_an_apps_ran_pattern_goes_by_the_same_rule(self):
        self.configure(slow=[
            {"name": "app-build", "paths": "^windows/Fixture\\.App/", "ran": "\\bdotnet\\s+build\\b.*Fixture\\.App"},
            {"name": "shots", "paths": "^macos/Fixture/Views/", "ran": "scripts/shots\\.py"},
        ])
        self.hook.setup(self.root)
        self.assert_ran("dotnet build windows/Fixture.App/Fixture.App.csproj", "app-build", "winui-build")
        self.assert_ran("cd windows && time dotnet build Fixture.App -v q", "app-build", "winui-build")
        self.assert_ran("scripts/shots.py --all", "shots")
        self.assert_ran("python3 -u scripts/shots.py --all && git status", "shots")
        for command in (
            'grep -rn "dotnet build Fixture.App" docs/',
            "echo dotnet build Fixture.App",
            'git commit -m "scripts/shots.py: every window"',
            "cat scripts/shots.py",
            "python3 scripts/lint.py scripts/shots.py",
            "cat > scripts/shots.py <<'EOF'\nprint('dotnet build Fixture.App')\nEOF",
            "dotnet build windows/Fixture.App | tail -5",
            "scripts/shots.py; echo done",
            "scripts/shots.py || true",
        ):
            self.assert_ran(command)
        # verify.sh notes the app's check as `bash -c <its run>` once it passed: every command of it counts.
        git(self.root, "init", "-q")
        note = f"{int(time.time())}\t\tbash -c cd windows && dotnet build Fixture.App -v q; echo built\n"
        (self.root / ".git" / self.hook.SLOW_LOG).write_text(note, "utf8", newline="\n")
        self.assertEqual(set(self.hook.verify_ran()), {"app-build", "winui-build"})

    def test_the_stop_reminder_stays_after_a_command_that_only_names_the_check(self):
        sessions = iter(range(100))

        def reminded_after(command):
            session = f"names{next(sessions)}"  # the log has whole seconds: one session per command
            self.run_hook("post", {"session_id": session, "tool_name": "Edit", "tool_input": {
                "file_path": str(self.root / "macos/FixtureCore/Sources/FixtureCore/Phrases.swift")}})
            self.run_hook("post", {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}})
            self.reminder = self.run_hook("stop", {"session_id": session}).stderr
            self.resumed = self.run_hook("resume", {"session_id": session}).stdout
            return "core package tests" in self.reminder

        self.assertTrue(reminded_after('grep -rn "swift test" docs/'))
        self.assertNotIn("it ran", self.reminder)
        self.assertTrue(reminded_after("swift test --package-path macos/FixtureCore 2>&1 | tail -5"))
        # The agent saw the tests pass and is told why that run isn't one: the reminder names the lost exit status.
        self.assertIn("core package tests (swift test --package-path macos/FixtureCore) [it ran, but that run doesn't "
                      "count: in the background, only some of its tests", self.reminder)
        self.assertNotIn("conformance.sh) [it ran", self.reminder, "only the check that ran")
        self.assertNotIn("swift-test", self.resumed, "and it isn't among the checks the session ran")
        self.assertNotIn("unjudged", self.resumed)
        self.assertFalse(reminded_after("cd macos && swift test --package-path FixtureCore"))
        self.assertIn("swift-test", self.resumed)
        # Codex's argv: the words of a message are one argument, and `bash -lc` carries the command.
        self.assertTrue(reminded_after(["git", "commit", "-m", "swift test passes"]))
        self.assertFalse(reminded_after(["bash", "-lc", "cd macos && swift test --package-path FixtureCore"]))

    def test_a_check_sent_to_the_background_has_not_run_yet(self):
        sessions = iter(range(100))

        def reminder_after(tool_input, response):
            session = f"later{next(sessions)}"
            self.run_hook("post", {"session_id": session, "tool_name": "Edit", "tool_input": {
                "file_path": str(self.root / "macos/FixtureCore/Sources/FixtureCore/Phrases.swift")}})
            self.run_hook("post", {"session_id": session, "tool_name": "Bash", "tool_input": tool_input,
                                   "tool_response": response})
            return self.run_hook("stop", {"session_id": session}).stderr

        command = "swift test --package-path macos/FixtureCore"
        self.assertNotIn("core package tests", reminder_after({"command": command}, {"stdout": "", "interrupted": False}))
        # Started in the background, or moved there when it ran too long: the hook heard it start, not how it ended.
        for tool_input, response in (({"command": command, "run_in_background": True}, {"backgroundTaskId": "b1"}),
                                     ({"command": command}, {"backgroundTaskId": "b2"}),
                                     ({"command": command}, {"timedOutAfterMs": 120000}),
                                     ({"command": command}, {"backgroundedToDeliverMessage": True})):
            self.assertIn("core package tests (swift test --package-path macos/FixtureCore) [it ran, but that run "
                          "doesn't count: in the background", reminder_after(tool_input, response), response)

    def test_a_command_is_split_where_the_shell_splits_it(self):
        parts = self.hook.shell_parts('cd a && X=1 time make -j4 2>&1 | tee "my log"; echo "$(date +%s)" > out.txt &')
        self.assertEqual([part.words for part in parts],
                         [["cd", "a"], ["X=1", "time", "make", "-j4"], ["tee", "my log"], ["date", "+%s"], ["echo", "$()"]])
        self.assertEqual(self.hook.run_words(parts[1].words), ["make", "-j4"])
        self.assertEqual([part.kept for part in parts], [False] * 5, "a pipe, a `;` with more after it, an `&`")
        self.assertEqual([part.captured for part in parts], [False, False, False, True, False])
        self.assertEqual([part.background for part in parts], [False, False, False, True, True])
        parts = self.hook.shell_parts("until gh run view 7 | grep -q completed; do sleep 30; done")
        self.assertEqual([(part.keywords, part.words) for part in parts], [
            (["until"], ["gh", "run", "view", "7"]), ([], ["grep", "-q", "completed"]), (["do"], ["sleep", "30"]),
            (["done"], [])])
        for loop in ("while true; do sleep 5; done &", "{ sleep 5; echo up; } &", "(sleep 5; echo up) &"):
            self.assertTrue(all(part.background for part in self.hook.shell_parts(loop)), loop)
        self.assertEqual(self.hook.program(["/usr/bin/env"]), "env")
        self.assertEqual(self.hook.shell_script(["/bin/zsh", "-lc", "ls | wc -l"]), "ls | wc -l")
        self.assertIsNone(self.hook.shell_script(["bash", "scripts/build.sh", "-c", "Release"]))

    def test_text_that_is_no_shell_command_splits_without_an_error(self):
        for odd in ("echo 'unclosed", 'echo "unclosed $(', "$(", "(((", ")", "a | | b", "<<", "a <<EOF", "`", "$'", "${",
                    "\\", "2>", ";;;", "", "(" * 5000, "$(" * 3000, "'" * 10001, "a;" * 3000, "case $x in a) swift test ;; esac",
                    "f() { swift test; }", "[[ ( -f a ) ]] && b"):
            self.assertIsInstance(self.hook.shell_parts(odd), list, odd[:20])
            self.assert_ran(odd)
        self.assert_ran(7)
        self.assert_ran(None)


class ForegroundWaits(HookTestCase):
    """`hook.py pre` refuses a foreground shell command that only waits (`foreground_wait`): nothing but a long `sleep`,
    or a loop around a `sleep` that waits for something (`while`/`until`, a `for` that `break`s). The agent sits in
    such a call and learns nothing until it ends. In the background the same command is a watcher, and a `sleep` the
    command only names is no wait at all."""

    POLL = "while ! gh run view 7 --json status -q .status | grep -q completed; do sleep 30; done"

    def setUp(self):
        super().setUp()
        self.hook = load_hook()
        self.hook.setup(self.root)

    def refusal(self, command, **tool_input):
        """What `hook.py pre` says to a Bash call: empty when the call passes."""
        event = {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": command, **tool_input}}
        done = self.run_hook("pre", event)
        self.assertEqual(done.returncode, 2 if done.stderr else 0, done.stderr)
        return done.stderr

    def assert_waits(self, *commands):
        for command in commands:
            self.assertTrue(self.hook.foreground_wait(command), command)

    def assert_passes(self, *commands):
        for command in commands:
            self.assertIsNone(self.hook.foreground_wait(command), command)

    def test_a_long_sleep_and_a_poll_loop_are_refused_with_what_to_do_instead(self):
        for command in ("sleep 300", self.POLL):
            message = self.refusal(command)
            self.assertIn("only waits", message, command)
            self.assertIn("in the background", message, command)
            self.assertIn("`run_in_background`", message, command)
            self.assertIn("the harness tells you when it exits", message, command)
            self.assertIn("`gh run watch <id> --exit-status`", message, command)
            self.assertEqual(self.refusal(command, run_in_background=False), message, "said to be in the foreground")
        self.assertIn("a foreground `sleep` for 300 seconds", self.refusal("sleep 300"))
        self.assertIn("A pause under 30 seconds between two steps is fine", self.refusal("sleep 300"))
        self.assertIn("a foreground loop around `sleep` that waits for something", self.refusal(self.POLL))

    def test_the_same_commands_in_the_background_pass(self):
        for command in ("sleep 300", self.POLL):
            self.assertEqual(self.refusal(command, run_in_background=True), "", command)
        self.assertEqual(self.refusal("sleep 300 &"), "")
        self.assertEqual(self.refusal(f"{self.POLL} &"), "")
        self.assertEqual(self.refusal("gh run watch 7 --exit-status", run_in_background=True), "", "the watcher itself")
        self.assert_passes(
            "nohup sleep 300 &",
            "(sleep 300; echo late) &",
            "{ until curl -sf localhost:8080/health; do sleep 1; done; } &",
            "( while true; do sleep 5; done ) &",
            "while true; do sleep 5; done > poll.log 2>&1 &",
            "sleep 300 & echo started",
        )

    def test_the_other_agents_shapes_are_read_too(self):
        self.assertTrue(self.refusal(["bash", "-lc", "sleep 300"]), "Codex: argv around the script")
        self.assertTrue(self.refusal(["sleep", "300"]))
        self.assertEqual(self.refusal(["sleep", "2"]), "")
        self.assertEqual(self.refusal(["git", "commit", "-m", "while true; do sleep 300; done"]), "")
        copilot = {"session_id": "t", "tool_name": "Bash", "tool_input": json.dumps({"command": self.POLL})}
        self.assertEqual(self.run_hook("pre", copilot).returncode, 2, "Copilot: tool_input as a JSON string")

    def test_what_only_waits(self):
        self.assert_waits(
            "sleep 30",
            "sleep 5m",
            "sleep 1m 30s",
            "sleep 0.5h",
            "sleep infinity",
            "/bin/sleep 45",
            "time sleep 60",
            "sleep 120; echo done",
            "echo waiting && sleep 90",
            "sleep 20; sleep 20",  # two pauses that add up
            "sleep 300 # for the build",
            "(sleep 300)",
            "bash -c 'sleep 300'",
            "if true; then sleep 300; fi",
            'echo "$(sleep 300)"',
            "until curl -sf localhost:8080/health; do sleep 1; done",
            "while true; do gh run view 7; sleep 45; done",
            "while sleep 5; do gh pr checks; done",
            "while [ ! -f out.log ]; do\n  sleep 2\ndone\ncat out.log",
            "while kill -0 $pid 2>/dev/null; do sleep 0.2; done",
            "while :; do sleep 1; done | tee poll.log",
            "bash -lc 'while true; do sleep 5; done'",
            "cd build && until [ -f done ]; do sleep 3; done && ls",
            # The loop around the `sleep` need not be the nearest one.
            "until [ -f done ]; do for step in 1 2; do sleep 1; done; done",
            "for run in 7 8; do while gh run view $run | grep -q in_progress; do sleep 20; done; done",
            # A `for` that leaves once it saw what it waits for is a poll too, when its rounds sleep long enough.
            "for i in $(seq 60); do gh run view 1 --json status -q .status | grep -q completed && break; sleep 30; done",
            "for i in {1..40}; do curl -sf localhost:3000 && break; sleep 3; done",
            "for i in `seq 10`; do if curl -sf localhost; then break; fi; sleep 5; done",
            "for attempt in 1 2 3; do curl -sf localhost && break; sleep 20; done",
            "for ((i = 0; i < 40; i++)); do curl -sf localhost && exit 0; sleep 3; done",
        )

    def test_a_pause_between_two_steps_passes(self):
        self.assert_passes(
            "sleep 5",
            "sleep 29",
            "sleep 0.5",
            "sleep 2; echo ok",
            "sleep 3 && git status",
            "open -a Simulator && sleep 10 && xcrun simctl list",
            "npm start & sleep 5; curl -s localhost:3000",
            "./server & sleep 45; curl -s localhost:3000",  # not only a sleep: the command does its own work after it
            "sleep $DELAY",  # no number to judge
            # Bounded loops and loops over lines are not polls.
            "for attempt in 1 2 3; do curl -sf localhost && break; sleep 5; done",  # 15 seconds at most
            "for i in {1..3}; do curl -sf localhost && break; sleep 2; done",
            "for f in a b c; do curl -s localhost/$f; sleep 20; done",  # it waits for nothing
            "for i in $(seq 60); do curl -sf localhost && break; sleep $DELAY; done",  # no number to judge
            "while read -r url; do curl -sf $url && break; sleep 1; done < list.txt",
            "while read -r line; do echo $line; sleep 1; done < list.txt",
            "cat list.txt | while IFS= read -r url; do curl -s $url; sleep 1; done",
            "echo start; while read x; do sleep 1; done",
            # No `sleep` at all, and words that only start like it.
            "gh run watch 7 --exit-status",
            "caffeinate -i swift test",
            "sleepwatcher --help",
            "man sleep",
            "which sleep",
        )

    def test_a_sleep_the_command_only_names_is_no_wait(self):
        self.assert_passes(
            # A script being written: a heredoc, with and without quotes around its end word, a printf, an echo.
            "cat > poll.sh <<'EOF'\nwhile true; do sleep 30; done\nEOF",
            "cat > wait.sh << EOF\nsleep 300\nEOF\nchmod +x wait.sh",
            "cat <<-EOF > wait.sh\n\tsleep 300\n\tEOF",
            "printf 'while true; do sleep 1; done\\n' > poll.sh",
            'echo "sleep 300" >> wait.sh',
            "tee wait.sh <<< 'sleep 300'",
            # Quoted strings: a commit message, a pattern, another machine's or another program's command.
            "git commit -m 'no more sleep 300 in the foreground'",
            'git commit -m "$(cat <<\'EOF\'\nwhile true; do sleep 300; done\nEOF\n)"',
            "grep -rn 'sleep 300' scripts/",
            "grep -n 'while true; do sleep 5; done' poll.sh",
            "ssh build-host 'sleep 300'",
            "python3 -c 'import time; time.sleep(300)'",
            "echo sleep 300",
            # Comments, and text that is no complete loop.
            "# sleep 300\nls",
            "ls  # while true; do sleep 1; done",
            "while true; do sleep 5",
            "echo while true; echo do sleep 300; echo done",
        )
        for odd in ("sleep", "sleep '", "sleep $(", "done; sleep 1; done", "while; done; sleep", "sleep " * 4000, 7, None):
            self.assertIsNone(self.hook.foreground_wait(odd), str(odd)[:20])

    def test_the_threshold_is_the_apps(self):
        self.configure(foregroundSleepSeconds=120)
        self.hook.setup(self.root)
        self.assert_passes("sleep 60", "sleep 119")
        self.assert_waits("sleep 120", "sleep 2m", self.POLL)
        self.assertIn("A pause under 120 seconds", self.hook.foreground_wait("sleep 300"))
        self.configure(foregroundSleepSeconds=0)
        self.assertEqual(self.refusal("sleep 300"), "", "0 turns the guard off")
        self.assertEqual(self.refusal(self.POLL), "")

    def test_a_command_that_passes_still_meets_the_other_guards(self):
        xcstrings = "macos/Fixture/Resources/Localizable.xcstrings"
        self.assertIn("String Catalog", self.refusal(f"sleep 2; cat {xcstrings}"))
        self.assertIn("String Catalog", self.refusal(f"sleep 300; cat {xcstrings}", run_in_background=True))


class ShellScripts(unittest.TestCase):
    """How the Stop hooks start verify.sh: itself on macOS and Linux; on Windows, which starts no shell script itself,
    through Git for Windows' bash, never WSL's."""

    def test_the_command_is_the_script_or_bash_with_it(self):
        script = TOOLS / "verify.sh"
        self.assertEqual(companion.shell_command(script, windows=False), [str(script)])
        with mock.patch.object(companion, "git_bash", return_value="bash.exe"):
            self.assertEqual(companion.shell_command(script, windows=True), ["bash.exe", script.as_posix()])
        with mock.patch.object(companion, "git_bash", return_value=None):
            self.assertIsNone(companion.shell_command(script, windows=True))

    def test_git_bash_is_the_one_beside_git_not_system32s(self):
        with tempfile.TemporaryDirectory() as temp:
            git = Path(temp).resolve() / "Git"
            for path in (git / "cmd" / "git.exe", git / "bin" / "bash.exe"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
            found = {"bash": r"C:\Windows\System32\bash.exe", "git": str(git / "cmd" / "git.exe")}
            with mock.patch.object(companion.shutil, "which", side_effect=found.get):
                self.assertEqual(companion.git_bash(), str(git / "bin" / "bash.exe"))
            with mock.patch.object(companion.shutil, "which", side_effect={"bash": "/usr/bin/bash"}.get):
                self.assertEqual(companion.git_bash(), "/usr/bin/bash")
            with mock.patch.object(companion.shutil, "which", return_value=None):
                self.assertIsNone(companion.git_bash())

    @unittest.skipUnless(VALIDATE.is_file(), "no .claude/hooks/validate.py in this checkout")
    def test_laching_s_validate_hook_runs_a_shell_script_check_through_bash_on_windows(self):
        validate = load(VALIDATE)
        script = str(REPO / "scripts" / "verify.sh")
        self.assertEqual(validate.command(script, ["--fast"], windows=False), [script, "--fast"])
        with mock.patch.object(validate, "git_bash", return_value="bash.exe"):
            self.assertEqual(validate.command(script, ["--fast"], windows=True),
                             ["bash.exe", Path(script).as_posix(), "--fast"])
            self.assertEqual(validate.command(sys.executable, ["-V"], windows=True), [sys.executable, "-V"])
        with mock.patch.object(validate, "git_bash", return_value=None):
            self.assertIsNone(validate.command(script, ["--fast"], windows=True))


class LongSession(HookTestCase):
    """`hook.py stop` past hooks.longSessionTokens: a note to the user, once per compaction cycle."""

    def transcript(self, tokens, compactions=0):
        path = Path(self.tmp) / "transcript.jsonl"
        lines = [{"type": "system", "subtype": "compact_boundary"}] * compactions
        lines += [{"type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": tokens - 1}}},
                  {"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 5}}}]
        path.write_text("".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines))
        return {"session_id": "long", "transcript_path": str(path)}

    def note(self, event):
        done = self.run_hook("stop", event)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)["systemMessage"] if done.stdout.strip() else None

    def test_tells_the_user_once_per_compaction_cycle(self):
        self.assertIsNone(self.note(self.transcript(90_000)))
        self.assertIn("150k tokens, and every call", self.note(self.transcript(150_000)) or "")
        self.assertIsNone(self.note(self.transcript(160_000)), "once")
        self.assertIn("140k tokens after 1 compaction,", self.note(self.transcript(140_000, compactions=1)) or "")

    def test_no_transcript_or_disabled(self):
        self.assertIsNone(self.note({"session_id": "codex"}))
        self.configure(longSessionTokens=0)
        self.assertIsNone(self.note(self.transcript(500_000)))

    def test_a_blocked_stop_asks_the_agent_to_pass_it_on(self):
        self.run_hook("post", {"session_id": "long", "tool_name": "Edit",
                               "tool_input": {"file_path": str(self.root / "macos/FixtureCore/Sources/FixtureCore/A.swift")}})
        done = self.run_hook("stop", self.transcript(150_000))
        self.assertEqual(done.returncode, 2)
        self.assertIn("Then tell the user in one line: This session's context is 150k", done.stderr)


class LinkReminder(HookTestCase):
    """`hook.py stop` while the app is linked to a Laching checkout: said once per session, and no failed check."""

    def setUp(self):
        super().setUp()
        targets = self.root / "windows" / "Directory.Build.targets"  # a link, as `companion.py link` marks it
        targets.write_text(f"<Project>\n  <!-- {companion.LINK_MARKER}: a trial -->\n</Project>\n", "utf8")

    def test_the_user_is_told_once_and_the_stop_goes_through(self):
        event = {"session_id": "linked"}
        done = self.run_hook("stop", event)
        self.assertEqual(done.returncode, 0, done.stderr)
        note = json.loads(done.stdout)["systemMessage"]
        self.assertIn("is linked to a Laching checkout (Directory.Build.targets), not to Laching's pinned release", note)
        self.assertIn("laching/tools/companion.py unlink` before committing.", note)
        again = self.run_hook("stop", event)
        self.assertEqual((again.returncode, again.stdout.strip()), (0, ""), "once a session")
        self.assertIn("is linked to", json.loads(self.run_hook("stop", {"session_id": "another"}).stdout)["systemMessage"])

    @unittest.skipIf(BASH is None, NO_SHELL)
    def test_the_link_alone_fails_no_fast_check(self):
        """While linked the pins pass with a warning (not in CI: the tests clear its variables), so an edit that needs
        no slow check stops; one that needs one is blocked for that, and the agent reads the reminder there."""
        env = mock.patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""})
        env.start()
        self.addCleanup(env.stop)
        edit = {"session_id": "fast", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / "notes.md")}}
        self.run_hook("post", edit)
        done = self.run_hook("stop", {"session_id": "fast"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("is linked to", json.loads(done.stdout)["systemMessage"])
        source = "macos/FixtureCore/Tests/FixtureCoreTests/Conformance/A.swift"  # the vectors cover it, no fast check
        self.run_hook("post", {"session_id": "slow", "tool_name": "Edit",
                               "tool_input": {"file_path": str(self.root / source)}})
        done = self.run_hook("stop", {"session_id": "slow"})
        self.assertEqual(done.returncode, 2)
        self.assertNotIn("Fast checks on the files you edited failed", done.stderr)
        self.assertIn("Not run since your last edit:", done.stderr)
        self.assertIn("companion.py unlink` before committing.", done.stderr)
        again = self.run_hook("stop", {"session_id": "slow"})
        self.assertNotIn("is linked to", again.stderr, "once a session")


@unittest.skipIf(BASH is None, NO_SHELL)
@unittest.skipIf(WINDOWS, "the fakes are sh scripts")
class VerifyNotesWhatRan(HookTestCase):
    """The real verify.sh and the Stop hook over the fixture app as a repository, with an Android port whose Gradle
    wrapper is a stand-in: a slow check stops being named when it ran and passed, and for no other reason."""

    CORE = "android/core/src/main/kotlin/laching/A.kt"
    APP = "android/app/src/main/kotlin/app/fixture/A.kt"

    def setUp(self):
        super().setUp()
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["android"] = {"dir": "android", "core": "core", "app": "app"}
        # The APK is the app's own check (verify.sh builds none): the hook knows it by its command.
        data["verify"] = {"checks": [{"name": "the APK builds", "paths": "^android/app/", "slow": True,
                                      "run": "cd android && ./gradlew -q :app:assembleDebug"}]}
        config.write_text(json.dumps(data), "utf8")
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        self.calls = self.root / "calls.log"
        self.fake(self.bin / "java", "exit 0\n")
        self.fake(self.bin / "dotnet", f'echo "dotnet $*" >> "{self.calls}"\n')
        (self.root / "android").mkdir()
        self.fake(self.root / "android" / "gradlew", f'echo "gradlew $*" >> "{self.calls}"\n'
                  'case " $* " in *" $GRADLE_FAILS "*) echo "$GRADLE_FAILS failed"; exit 1 ;; esac\n')
        git(self.root, "init", "-q")
        self.log = self.root / ".git" / "laching-slow-checks.log"
        self.hook = load_hook()
        self.hook.setup(self.root)

    @staticmethod
    def fake(path, body):
        with path.open("w", encoding="utf8", newline="\n") as f:
            f.write("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def verify(self, *args, **env):
        """tools/verify.sh in the app, with the fakes first on $PATH and without the variables of a git running the
        tests (a hook's GIT_DIR would put the note in Laching's own Git folder)."""
        clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        return subprocess.run([*shell(TOOLS / "verify.sh"), *args], cwd=self.root, capture_output=True, encoding="utf8",
                              env={**clean, "PATH": search_path(self.bin), "HOME": str(self.root), **env}, timeout=120)

    def edit(self, session, *paths):
        for path in paths:
            self.run_hook("post", {"session_id": session, "tool_name": "Edit",
                                   "tool_input": {"file_path": str(self.root / path)}})

    def named(self, session):
        """The slow checks the Stop hook says weren't run, as it words them."""
        said = self.run_hook("stop", {"session_id": session}).stderr
        return [label for label in ("Kotlin core tests", "Android app build", "Windows app model tests", "WinUI app build")
                if label in said]

    def noted(self):
        """The kinds column of each line verify.sh left, in order."""
        if not self.log.exists():
            return []
        return [line.split("\t")[1] for line in self.log.read_text("utf8").splitlines()]

    def test_only_a_check_that_ran_and_passed_is_noted(self):
        self.edit("android", self.CORE, self.APP)
        both = ["Kotlin core tests", "Android app build"]
        self.assertEqual(self.named("android"), both)

        usage = self.verify("--help")
        self.assertEqual(usage.returncode, 0, usage.stderr)
        fast = self.verify("--fast", "--", self.CORE, self.APP)
        self.assertEqual(fast.returncode, 0, fast.stdout + fast.stderr)
        self.assertEqual(self.noted(), [], "--help and --fast run no slow check")
        self.assertFalse(self.calls.exists(), "nor Gradle")
        for command in ("laching/tools/verify.sh --help", "cat laching/tools/verify.sh", "laching/tools/verify.sh"):
            self.run_hook("post", {"session_id": "android", "tool_name": "Bash", "tool_input": {"command": command}})
        self.assertEqual(self.named("android"), both, "naming verify.sh runs nothing")

        failed = self.verify("--", self.CORE, self.APP, GRADLE_FAILS=":core:test")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("FAIL  gradlew :core:test", failed.stdout)
        self.assertIn("ok    the APK builds", failed.stdout)
        self.assertEqual(self.noted(), [""], "the check that passed beside the one that failed")
        self.assertEqual(self.named("android"), ["Kotlin core tests"], "a failed run leaves its check unrun")

        self.log.unlink()
        failed = self.verify("--", self.CORE, self.APP, GRADLE_FAILS=":app:assembleDebug")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertEqual(self.noted(), ["gradle-core-test"])
        self.assertEqual(self.named("android"), ["Android app build"])

    def test_a_full_passing_run_notes_the_checks_that_ran_and_no_others(self):
        self.edit("android", self.CORE, self.APP)
        done = self.verify("--", self.CORE, self.APP)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ok    gradlew :core:test", done.stdout)
        self.assertIn("ok    the APK builds", done.stdout)
        self.assertEqual(self.noted(), ["gradle-core-test", ""])
        stamp, _, command = self.log.read_text("utf8").splitlines()[1].split("\t")
        self.assertTrue(stamp.isdigit())
        self.assertEqual(command, "bash -c cd android && ./gradlew -q :app:assembleDebug")
        self.assertEqual(set(self.hook.verify_ran()), {"gradle-core-test", "android-build"}, "the Android build included")
        self.assertEqual(self.named("android"), [])
        self.assertEqual(self.run_hook("stop", {"session_id": "android"}).returncode, 0)
        resumed = self.run_hook("resume", {"session_id": "android"})
        self.assertIn("Slow checks this session ran: gradle-core-test, android-build", resumed.stdout)

    def test_no_vm_leaves_the_winui_build_unrun(self):
        view = "windows/Fixture.App/Views/HomeView.xaml"
        self.edit("windows", view)
        self.assertEqual(self.named("windows"), ["Windows app model tests", "WinUI app build"])
        done = self.verify("--no-vm", "--", view)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("skip  WinUI app: builds on Windows or in the Parallels VM (left alone: --no-vm)", done.stdout)
        self.assertEqual(self.noted(), ["dotnet-app-test"], "a skipped check isn't one that ran")
        self.assertEqual(self.named("windows"), ["WinUI app build"])

    def test_an_app_check_that_exits_77_skipped_itself(self):
        """A machine without the toolchain: the app's own check says why and exits 77, which is neither a failure nor
        a run, where an early `exit 0` would count the APK as built."""
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["verify"]["checks"][0]["run"] = "echo looking; echo '  skipped: needs the Android SDK'; echo; exit 77"
        data["verify"]["checks"].append({"name": "the manifest", "paths": "^android/app/",
                                         "run": "echo 'skipped: needs aapt'; exit 77"})
        config.write_text(json.dumps(data), "utf8")
        self.edit("sdk", self.APP)
        self.assertEqual(self.named("sdk"), ["Android app build"])
        done = self.verify("--", self.APP)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("skip  the APK builds: skipped: needs the Android SDK\n", done.stdout)
        self.assertIn("skip  the manifest: skipped: needs aapt\n", done.stdout, "a fast check skips itself the same way")
        self.assertNotIn("ok    the", done.stdout)
        self.assertNotIn("FAIL", done.stdout)
        self.assertEqual(self.noted(), [], "a check that skipped itself isn't one that ran")
        self.assertEqual(self.named("sdk"), ["Android app build"], "so the hook still asks for it")

        # Only the app's own checks skip themselves this way, and any other status is still a failure.
        data["verify"]["checks"][0]["run"] = "echo 'no SDK'; exit 78"
        config.write_text(json.dumps(data), "utf8")
        failed = self.verify("--", self.APP)
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("FAIL  the APK builds", failed.stdout)
        self.assertEqual(self.noted(), [])

    def test_verify_runs_a_built_in_check_for_the_paths_the_app_gives_it(self):
        """`hooks.slow` gives a built-in check other paths: the hook reminds of it for them and names verify.sh as what
        to run, so verify.sh runs the check for them too and the reminder clears."""
        # A pattern as an app writes one: the hook reads it as a Python regular expression, lookahead and all.
        paths = r"^(?!.*\.(?i:md|markdown|rst|adoc)$)(?:^android/core/|^android/gradle/)"
        self.configure(slow=[{"name": "gradle-core-test", "paths": paths}])
        catalog, guide = "android/gradle/libs.versions.toml", "android/gradle/README.md"
        self.edit("guide", guide)
        self.assertEqual(self.named("guide"), [], "the app's pattern leaves documentation out")
        done = self.verify("--", guide)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("gradlew", done.stdout)
        self.edit("catalog", catalog)
        self.assertEqual(self.named("catalog"), ["Kotlin core tests"])
        done = self.verify("--", catalog)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("grep", done.stdout + done.stderr)
        self.assertIn("ok    gradlew :core:test", done.stdout)
        self.assertEqual(self.noted(), ["gradle-core-test"])
        self.assertEqual(self.named("catalog"), [])


class Resume(HookTestCase):
    """`hook.py resume`: the session's edits and checks from its log, then the repository's state."""

    def setUp(self):
        super().setUp()
        os.environ["TMPDIR"] = self.tmp
        self.hook = load_hook()
        self.hook.setup(self.root)
        for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "fixture app", "--no-verify"]):
            git(self.root, *args)

    def test_names_each_edited_file_once_and_the_checks_run(self):
        event = {"session_id": "resume"}
        self.hook.state_file(event).write_text("1\tedit\tmacos/Fixture/Views/HomeView.swift\n"
                                              "2\tran\tconformance\n"
                                              "3\tedit\twindows/Fixture.Core/Config.cs\n"
                                              "4\tedit\tmacos/Fixture/Views/HomeView.swift\n")
        done = self.run_hook("resume", event)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count("macos/Fixture/Views/HomeView.swift"), 1, done.stdout)
        self.assertLess(done.stdout.index("HomeView.swift"), done.stdout.index("windows/Fixture.Core/Config.cs"))
        self.assertIn("Slow checks this session ran: conformance", done.stdout)
        self.assertIn("(branch ", done.stdout)
        self.assertIn("fixture app", done.stdout)  # the recent commits
        self.assertNotIn("git status --short", done.stdout)  # a clean tree has no status block

    def test_a_clean_tree_without_a_log_prints_nothing(self):
        done = self.run_hook("resume", {"session_id": "fresh"})
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        with (self.root / "macos/scripts/sounds.py").open("a") as f:  # a dirty tree alone is worth a report
            f.write("# changed\n")
        done = self.run_hook("resume", {"session_id": "fresh"})
        self.assertIn("git status --short:\n   M macos/scripts/sounds.py", done.stdout)
        self.assertNotIn("Files this session edited", done.stdout)


if __name__ == "__main__":
    unittest.main()
