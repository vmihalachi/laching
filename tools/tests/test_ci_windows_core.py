"""The portable Windows CI preserves app paths, optional tests, strict exports and shared failure handling."""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from support import TOOLS, FixtureAppTestCase

sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402
import companion_ci  # noqa: E402

spec = importlib.util.spec_from_file_location("ci_windows_core", TOOLS / "ci-windows-core.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class WindowsCoreCITests(FixtureAppTestCase):
    def config(self, **windows):
        config = companion.load()
        config.data["windows"].update({"coreTests": "Core Tests", **windows})
        return config

    def test_paths_remain_single_arguments_without_project_or_folder_name_assumptions(self):
        config = self.config(dir="Desktop Code; $(touch unwanted)", coreTests="Portable tests", appTests="Model Tests")
        for stage, folder in (("core", "Portable tests"), ("app", "Model Tests")):
            self.assertEqual(ci.commands(config, stage),
                             [(self.root, ["dotnet", "test", str(config.require_path("windows.dir") / folder), "-c", "Release"])])
        [(cwd, argv)] = ci.commands(config, "format")
        self.assertEqual(cwd, self.root)
        self.assertEqual(argv[3], str(config.require_path("windows.dir")))
        self.assertFalse((self.root / "unwanted").exists())

    def test_optional_model_and_winui_checks_follow_metadata_but_core_tests_are_required(self):
        config = self.config()
        self.assertEqual(Path(ci.commands(config, "lint")[0][1][1]), TOOLS / "windows/check-winui.py")
        config.data["windows"].pop("appTests")
        config.data["windows"].pop("app")
        self.assertEqual(ci.commands(config, "app"), [])
        self.assertEqual(ci.commands(config, "lint"), [])
        config.data["windows"].pop("coreTests")
        with self.assertRaisesRegex(companion.ConfigError, "windows.coreTests"):
            ci.commands(config, "core")

    def test_content_checks_strings_and_only_configured_phrases(self):
        config = self.config()
        self.assertEqual([Path(argv[1]).name for _, argv in ci.commands(config, "content")], ["l10n.py", "phrases.py"])
        config.data.pop("conformance")
        self.assertEqual([Path(argv[1]).name for _, argv in ci.commands(config, "content")], ["l10n.py"])
        self.assertEqual(ci.commands(config, "pins"), companion_ci.pins(config, TOOLS))

    def test_exports_compare_the_complete_tree_ignoring_only_crlf(self):
        config = self.config(strings="Localized Resources; $(touch unwanted)")
        output = config.work_dir / "isolated export"
        export, compare = ci.commands(config, "strings", output)
        self.assertEqual(export[1][-3:], ["export-windows", "--out", str(output)])
        self.assertEqual(compare[1], ["diff", "-r", "--strip-trailing-cr", str(output),
                                      str(config.require_path("windows.strings"))])
        output.mkdir(parents=True)
        expected = config.require_path("windows.strings")
        expected.mkdir(parents=True)
        (output / "Resources.resw").write_bytes(b"one\ntwo\n")
        (expected / "Resources.resw").write_bytes(b"one\r\ntwo\r\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(companion_ci.run(config, [compare], "ci-compare.log"), 0)
            (expected / "unexpected.resw").write_text("extra")
            self.assertEqual(companion_ci.run(config, [compare], "ci-compare.log"), 1)
        self.assertIn("unexpected.resw", (config.work_dir / "ci-compare.log").read_text())

    def test_export_run_owns_a_fresh_temporary_folder_and_cleans_it(self):
        config = self.config()
        exports = []
        def capture(config, commands, log_name):
            folder = Path(commands[0][1][-1])
            self.assertTrue(folder.is_dir())
            self.assertEqual(list(folder.iterdir()), [])
            (folder / "old-language.resw").write_text("old")
            exports.append(folder)
            return 0
        with mock.patch.object(companion_ci, "run", side_effect=capture):
            self.assertEqual(ci.run(config, "strings"), 0)
            self.assertEqual(ci.run(config, "strings"), 0)
        self.assertNotEqual(exports[0], exports[1])
        self.assertTrue(all(not path.exists() for path in exports))

    def test_shared_runner_stops_on_failure_and_logs_output_without_shell(self):
        config = self.config()
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.StringIO("useful failure detail\n")
        process.wait.return_value = 9
        with mock.patch.object(companion_ci.subprocess, "Popen", return_value=process) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(ci.run(config, "content"), 9)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((config.work_dir / "ci-windows-core-content.log").read_text(), "useful failure detail\n")
        self.assertEqual(run.call_args.kwargs["env"]["COMPANION_ROOT"], str(self.root))
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_driver_rejects_another_tool_checkout_and_accepts_the_pinned_path(self):
        config = self.config()
        parser = argparse.ArgumentParser()
        with mock.patch.object(companion, "load", return_value=config), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                companion_ci.load_config(parser, TOOLS)
            self.assertIs(companion_ci.load_config(parser, config.require_path("laching.path") / "tools"), config)

    def test_workflow_keeps_inputs_out_of_shell_source_and_runs_every_stage(self):
        workflow = (TOOLS.parent / ".github/workflows/windows-core.yml").read_text()
        run_lines = [line for line in workflow.splitlines() if "run:" in line]
        self.assertTrue(all("${{" not in line for line in run_lines))
        for stage in ci.STAGES:
            self.assertIn(f'python3 "./$LACHING_PATH/tools/ci-windows-core.py" {stage}', workflow)
        self.assertIn("fromJSON(inputs.runner)", workflow)
        self.assertIn('git submodule update --init -- "$LACHING_PATH"', workflow)
        self.assertIn('path: "**/build/ci-windows-core-*.log"', workflow)
