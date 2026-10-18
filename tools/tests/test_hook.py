#!/usr/bin/env python3
"""Tests for tools/hooks/hook.py, which runs before and after every tool call in a companion app: a bad pattern there
quietly blocks or lets through things in every session. Runs the hook over the fixture app."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from support import BASH, NO_SHELL, REPO, TOOLS, FixtureAppTestCase, companion, git

HOOK = TOOLS / "hooks" / "hook.py"
VALIDATE = REPO / ".claude" / "hooks" / "validate.py"  # Laching's own Stop hook, beside the apps' hook.py


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hook():
    return load(HOOK)


class HookTestCase(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()

    def run_hook(self, mode, event, payload=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root), "TMPDIR": self.tmp}
        return subprocess.run([sys.executable, str(HOOK), mode], input=payload if payload is not None else json.dumps(event),
                              text=True, capture_output=True, env=env, timeout=60)

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
            "macos/Fixture/Resources/Assets.xcassets/AppIcon.appiconset/icon_16x16.png",  # the app's own renderer
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

    def test_bad_input_and_a_bad_companion_json_fail_open(self):
        self.assertEqual(self.run_hook("pre", None, payload="not json").returncode, 0)
        for odd in ([1, 2], {"tool_name": "Read", "tool_input": 7}, {"tool_name": "apply_patch", "tool_input": None}):
            done = self.run_hook("pre", odd)
            self.assertEqual(done.returncode, 0, done.stderr)
        (self.root / "companion.json").write_text("{}", "utf8")
        event = {"session_id": "t", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / "conformance/phrases.json")}}
        self.assertEqual(self.run_hook("pre", event).returncode, 0)


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
        self.assertNotRegex("windows/Fixture.Core/A.cs", kinds["winui-build"])
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
        self.assertFalse(reminded_after(core, "laching/tools/verify.sh", "Kotlin core tests"), "the full verify")
        self.assertTrue(reminded_after(app, None, "Android app build"))
        self.assertFalse(reminded_after(app, "cd android && ./gradlew :app:assembleDebug", "Android app build"))
        self.assertFalse(reminded_after(app, "cd android && ./gradlew build", "Android app build"))
        self.assertTrue(reminded_after(app, "cd android && ./gradlew :core:test", "Android app build"))
        self.assertTrue(reminded_after(app, "laching/tools/verify.sh", "Android app build"), "verify.sh builds no APK")

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

    def test_app_model_tests_count_when_their_project_or_the_solution_ran(self):
        edit = {"session_id": "app", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "windows/Fixture.App/App/AppModel.cs")}}

        def reminded_after(command):
            self.run_hook("post", edit)
            self.run_hook("post", {"session_id": "app", "tool_name": "Bash", "tool_input": {"command": command}})
            return "Windows app model tests" in self.run_hook("stop", {"session_id": "app"}).stderr

        self.assertTrue(reminded_after("dotnet test windows/Fixture.Core.Tests"), "another test project")
        self.assertFalse(reminded_after("dotnet test windows/Fixture.App.Tests"))
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
                        "dotnet publish windows/Fixture.App/Fixture.App.csproj -c Release"):
            self.assertFalse(reminded_after(command), command)
        for command in ("laching/tools/windows/vm.sh status", "laching/tools/windows/vm.sh sync",
                        "dotnet build windows/Fixture.Core"):
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
        event = {"session_id": "stop", "tool_name": "Edit",
                 "tool_input": {"file_path": str(self.root / "macos/FixtureCore/Sources/FixtureCore/A.swift")}}
        self.assertEqual(self.run_hook("post", event).returncode, 0)
        done = self.run_hook("stop", {"session_id": "stop"})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("Not run since your last edit: conformance vectors", done.stderr)
        # Laching's own (the fixture has no scripts/ wrapper), wherever this checkout of it is.
        self.assertIn("tools/verify.sh -- macos/FixtureCore/", done.stderr)
        self.run_hook("post", {"session_id": "stop", "tool_name": "Bash", "tool_input": {"command": "scripts/verify.sh"}})
        self.assertEqual(self.run_hook("stop", {"session_id": "stop"}).returncode, 0)

    @unittest.skipIf(BASH is None, NO_SHELL)
    def test_stop_reports_what_the_fast_checks_reject(self):
        # The app's scripts/verify.sh wrapper, which the hook takes over Laching's; on Windows it runs through bash.
        wrapper = self.root / "scripts" / "verify.sh"
        wrapper.parent.mkdir()
        wrapper.write_text('#!/bin/sh\necho "lint: rejected $*"\nexit 1\n', "utf8", newline="\n")
        wrapper.chmod(0o755)
        edited = "macos/FixtureCore/Sources/FixtureCore/A.swift"
        event = {"session_id": "rejected", "tool_name": "Edit", "tool_input": {"file_path": str(self.root / edited)}}
        self.assertEqual(self.run_hook("post", event).returncode, 0)
        done = self.run_hook("stop", {"session_id": "rejected"})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn(f"Fast checks on the files you edited failed:\nlint: rejected --fast -- {edited}", done.stderr)

    def test_stop_says_so_when_windows_has_no_bash(self):
        self.hook.record(self.event, "edit", "macos/FixtureCore/Sources/FixtureCore/A.swift")
        stderr = io.StringIO()
        with mock.patch.object(self.hook.companion, "shell_command", return_value=None), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                self.hook.stop(self.event)
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("didn't run: Windows starts no shell script itself and there's no Git for Windows bash here",
                      stderr.getvalue())


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
