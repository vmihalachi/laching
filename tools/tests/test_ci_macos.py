"""The shared Mac CI commands preserve configured paths as arguments, feature gates and failure logs."""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from support import TOOLS, FixtureAppTestCase

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

    def test_swift_format_covers_core_and_shared_sources(self):
        config = self.config()
        for folder in ("Shared", "FixtureTests", "FixtureCore/Sources", "FixtureCore/Tests"):
            (config.mac_dir / folder).mkdir(parents=True, exist_ok=True)
        [(cwd, argv)] = ci.commands(config, "format")
        self.assertEqual(cwd, self.root)
        for folder in ("Fixture", "FixtureTests", "FixtureCore/Sources", "FixtureCore/Tests", "Shared"):
            self.assertIn(str(config.mac_dir / folder), argv)
        self.assertNotIn(str(config.mac_dir / "iOS"), argv, "missing optional platform roots aren't passed to swift-format")

    def test_failed_command_stops_stage_and_keeps_log(self):
        config = self.config()
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.StringIO("useful failure detail\n")
        process.wait.return_value = 9
        with mock.patch.object(ci.subprocess, "Popen", return_value=process) as run, redirect_stdout(io.StringIO()):
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
