"""The shared Mac CI commands preserve configured paths as arguments, feature gates and failure logs."""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from support import TOOLS, WINDOWS, FixtureAppTestCase

sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402

spec = importlib.util.spec_from_file_location("ci_macos", TOOLS / "ci-macos.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class MacCITests(FixtureAppTestCase):
    def config(self, **mac):
        config = companion.load()
        config.data["mac"].update(mac)
        return config

    def test_project_and_scheme_names_are_arguments_even_with_shell_characters(self):
        scheme = "Two words; $(touch unwanted)\n--another-option"
        config = self.config(project="Two words; hi.xcodeproj", scheme=scheme, projectSpec="My project.yml")
        [(cwd, command)] = ci.commands(config, "test")
        self.assertEqual(cwd, config.mac_dir)
        self.assertEqual(command[command.index("-scheme") + 1], scheme)
        self.assertEqual(command[command.index("-project") + 1], "Two words; hi.xcodeproj")
        self.assertEqual(command[command.index("-derivedDataPath") + 1], str(config.derived_data))
        generate, compare = ci.commands(config, "project")
        self.assertEqual(generate[1][-1], str(config.mac_dir / "My project.yml"))
        self.assertEqual(compare[1][-1], str(config.mac_dir / "Two words; hi.xcodeproj"))
        self.assertEqual(compare[1][-2], "--")
        self.assertFalse((self.root / "unwanted").exists())

    def test_optional_features_gate_content_and_core_checks(self):
        config = self.config()
        commands = ci.commands(config, "content")
        self.assertEqual([Path(argv[1]).name for _, argv in commands],
                         ["l10n.py", "phrases.py", "synth.py", "voice.py"])
        config.data["characters"] = {"design": "design"}
        self.assertIn("character.py", [Path(argv[1]).name for _, argv in ci.commands(config, "content")])
        for key in ("sounds", "voice", "characters", "conformance"):
            config.data.pop(key, None)
        config.data["mac"].pop("core")
        config.data["mac"].pop("projectSpec")
        self.assertEqual(ci.commands(config, "content"), [(config.root, [sys.executable, str(TOOLS / "l10n.py"), "check"])])
        self.assertEqual(ci.commands(config, "core"), [])
        self.assertEqual(ci.commands(config, "project"), [])

    def test_swift_format_uses_all_explicit_roots_without_layout_assumptions(self):
        roots = ["Application Code", "Library/Sources", "Phone App", "Cross Platform", "Testing", "Package.swift"]
        config = self.config(format=roots + [roots[0]])
        for folder in roots[:-1]:
            (config.mac_dir / folder).mkdir(parents=True, exist_ok=True)
        (config.mac_dir / roots[-1]).write_text("// manifest\n")
        [(cwd, argv)] = ci.commands(config, "format")
        self.assertEqual(cwd, self.root)
        self.assertEqual(argv[argv.index("--configuration") + 2:], [str(config.mac_dir / path) for path in roots])

    def test_format_fails_when_roots_are_absent_empty_or_stale(self):
        config = self.config()
        del config.data["mac"]["format"]
        with self.assertRaisesRegex(companion.ConfigError, "mac.format"):
            ci.commands(config, "format")
        for roots in ([], ["Renamed Sources"]):
            with self.subTest(roots=roots), self.assertRaisesRegex(companion.ConfigError, "mac.format"):
                ci.commands(self.config(format=roots), "format")

    def test_ios_tests_need_an_ios_scheme(self):
        config = self.config()
        self.assertEqual(ci.commands(config, "ios"), [])
        scheme = "Phone; $(touch unwanted)"
        config = self.config(iosScheme=scheme)
        with mock.patch.object(ci, "ios_destination", return_value="platform=iOS Simulator,id=1234"):
            [(cwd, argv)] = ci.commands(config, "ios")
        self.assertEqual(cwd, config.mac_dir)
        self.assertEqual(argv[argv.index("-scheme") + 1], scheme)
        self.assertEqual(argv[argv.index("-destination") + 1], "platform=iOS Simulator,id=1234")
        self.assertEqual(argv[argv.index("-derivedDataPath") + 1], str(config.root / "macos/build/dd-ios.noindex"))
        self.assertEqual(argv[-1], "test")

    def test_vectors_regenerate_and_diff_the_conformance_folder_new_files_included(self):
        config = self.config()
        vectors = str(config.root / "conformance")
        self.assertEqual(ci.commands(config, "vectors"), [
            (config.root, [str(TOOLS / "conformance.sh")]),
            (config.root, ["git", "add", "--intent-to-add", "--", vectors]),
            (config.root, ["git", "diff", "--exit-code", "--stat", "--", vectors])])
        config.data.pop("conformance")
        self.assertEqual(ci.commands(config, "vectors"), [])

    def test_release_builds_the_mac_app_ad_hoc_and_the_ios_app_unsigned(self):
        [(cwd, mac)] = ci.commands(self.config(), "release")
        self.assertEqual(cwd, self.config().mac_dir)
        self.assertEqual(mac[mac.index("-configuration") + 1], "Release")
        self.assertEqual(mac[mac.index("-destination") + 1], "platform=macOS")
        self.assertEqual(mac[-4:], ["CODE_SIGN_STYLE=Manual", "CODE_SIGN_IDENTITY=-", "DEVELOPMENT_TEAM=", "build"])
        _, ios = ci.commands(self.config(iosScheme="FixtureiOS"), "release")[1]
        self.assertEqual(ios[ios.index("-scheme") + 1], "FixtureiOS")
        self.assertEqual(ios[ios.index("-destination") + 1], "generic/platform=iOS")
        self.assertEqual(ios[-2:], ["CODE_SIGNING_ALLOWED=NO", "build"])
        self.assertNotEqual(ios[ios.index("-derivedDataPath") + 1], mac[mac.index("-derivedDataPath") + 1])

    def test_smoke_launches_the_release_build_of_mac_app(self):
        config = self.config()
        [(cwd, argv)] = ci.commands(config, "smoke")
        [(_, build)] = ci.commands(config, "release")
        products = Path(build[build.index("-derivedDataPath") + 1]) / "Build/Products/Release"
        self.assertEqual(argv, [str(TOOLS / "smoke-release.sh"), str(products / "Fixture.app")])
        config.data["mac"].pop("app")
        self.assertEqual(ci.commands(config, "smoke"), [])

    def test_failed_command_stops_stage_and_keeps_log(self):
        config = self.config()
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.StringIO("useful failure detail\n")
        process.wait.return_value = 9
        with mock.patch.object(ci.companion_ci.subprocess, "Popen", return_value=process) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(ci.run(config, "content"), 9)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((config.work_dir / "ci-content.log").read_text(), "useful failure detail\n")
        self.assertEqual(run.call_args.kwargs["env"]["COMPANION_ROOT"], str(self.root))
        self.assertIsInstance(run.call_args.args[0], list)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_reusable_workflow_uses_env_for_path_input_and_has_no_command_input(self):
        workflow = (TOOLS.parent / ".github/workflows/macos-app.yml").read_text()
        run_lines = [line for line in workflow.splitlines() if "run:" in line]
        self.assertTrue(all("${{" not in line for line in run_lines), "inputs never interpolate into shell source")
        self.assertIn('python3 "./$LACHING_PATH/tools/ci-macos.py"', workflow)
        self.assertIn("fromJSON(inputs.runner)", workflow)
        self.assertIn('path: "**/build/ci-*.log"', workflow)
        for stage in ci.STAGES:
            self.assertIn(f'tools/ci-macos.py" {stage}\n', workflow)
        self.assertIn("if: inputs.release && inputs.hosted\n        run: python3 \"./$LACHING_PATH/tools/ci-macos.py\" smoke",
                      workflow, "the Release app launches on GitHub's runner only")


@unittest.skipIf(WINDOWS, "a Mac tool")
class SmokeReleaseTests(FixtureAppTestCase):
    """smoke-release.sh launches mac.app's executable and refuses to run where the Release app would share real data."""

    def smoke(self, quits: bool, **env):
        binary = self.root / "Fixture.app/Contents/MacOS/Fixture"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho started\n" + ("exit 3\n" if quits else "exec sleep 30\n"))
        binary.chmod(0o755)
        (self.root / "home").mkdir()
        env = {**os.environ, "HOME": str(self.root / "home"), "CI": "true", "RUNNER_ENVIRONMENT": "github-hosted", **env}
        return subprocess.run([str(TOOLS / "smoke-release.sh"), str(binary.parents[2]), "1"], env=env,
                              capture_output=True, encoding="utf8")

    def test_a_running_app_passes(self):
        done = self.smoke(quits=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Running after 1 s.", done.stdout)

    def test_an_app_that_quits_fails_with_its_output(self):
        done = self.smoke(quits=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("exit status 3", done.stderr)
        self.assertIn("started", done.stderr)

    def test_refuses_outside_ci_and_on_a_self_hosted_runner(self):
        for env in ({"CI": ""}, {"RUNNER_ENVIRONMENT": "self-hosted"}):
            with self.subTest(env=env):
                done = self.smoke(quits=False, **env)
                self.assertEqual(done.returncode, 2)
                self.assertNotIn("started", done.stdout)
                for path in (self.root / "Fixture.app", self.root / "home"):
                    shutil.rmtree(path)
