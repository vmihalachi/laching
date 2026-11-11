"""The portable Windows CI preserves app paths, optional tests, strict exports and shared failure handling."""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from support import NO_SHEBANG, TOOLS, WINDOWS, FixtureAppTestCase, search_path

sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402
import companion_ci  # noqa: E402

spec = importlib.util.spec_from_file_location("ci_windows_core", TOOLS / "ci-windows-core.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

# The .NET host's choice of SDK, with 11.0.402 installed: the global.json nearest the working directory (walking up)
# pins it, and a pin that isn't 11.0.402 fails as the host does. Logs where it ran and its arguments.
FAKE_DOTNET = """#!/bin/sh
dir=$(pwd -P)
while :; do
  if [ -f "$dir/global.json" ]; then
    grep -q '"11.0.402"' "$dir/global.json" || {{ echo "A compatible .NET SDK was not found."; exit 155; }}
    break
  fi
  [ "$dir" = / ] && break
  dir=$(dirname "$dir")
done
echo "$(pwd -P) $*" >>"{log}"
"""


class WindowsCoreCITests(FixtureAppTestCase):
    def config(self, **windows):
        config = companion.load()
        config.data["windows"].update({"coreTests": "Core Tests", **windows})
        return config

    def test_paths_remain_single_arguments_without_project_or_folder_name_assumptions(self):
        config = self.config(dir="Desktop Code; $(touch unwanted)", coreTests="Portable tests", appTests="Model Tests")
        windows = config.require_path("windows.dir")
        # dotnet runs in the Windows folder, so its global.json picks the SDK; the paths are relative to it.
        for stage, folder in (("core", "Portable tests"), ("app", "Model Tests")):
            self.assertEqual(ci.commands(config, stage), [(windows, ["dotnet", "test", folder, "-c", "Release"])])
        self.assertEqual(ci.commands(config, "format"),
                         [(windows, ["dotnet", "format", "whitespace", ".", "--folder", "--verify-no-changes"])])
        config.data["windows"]["coreTests"] = "../Shared Tests"  # outside it: still relative to the Windows folder
        self.assertEqual(ci.commands(config, "core"),
                         [(windows, ["dotnet", "test", "../Shared Tests", "-c", "Release"])])
        self.assertFalse((self.root / "unwanted").exists())

    @unittest.skipIf(WINDOWS, NO_SHEBANG)
    def test_the_windows_folder_s_global_json_picks_the_sdk_or_fails_the_stage(self):
        bin_dir, log = self.root.parent / "bin", self.root.parent / "dotnet.log"
        bin_dir.mkdir()
        (bin_dir / "dotnet").write_text(FAKE_DOTNET.format(log=log), "utf8")
        (bin_dir / "dotnet").chmod(0o755)
        config = self.config()
        windows = config.require_path("windows.dir")
        pin = lambda version: (windows / "global.json").write_text(
            f'{{"sdk": {{"version": "{version}", "rollForward": "disable"}}}}', "utf8")
        with mock.patch.dict(os.environ, {"PATH": search_path(bin_dir)}), redirect_stdout(io.StringIO()):
            self.assertEqual(ci.run(config, "core"), 0, "no global.json: the newest SDK, as before")
            pin("11.0.402")
            self.assertEqual(ci.run(config, "core"), 0, "a pin the installed SDK meets")
            self.assertEqual(ci.run(config, "format"), 0)
            pin("11.0.999")
            self.assertEqual(ci.run(config, "core"), 155)
            self.assertEqual(ci.run(config, "format"), 155)
            # From the root, as the stages ran before, the host never saw windows/global.json.
            root_run = (self.root, ["dotnet", "test", "windows/Core Tests"])
            self.assertEqual(companion_ci.run(config, [root_run], "root.log"), 0)
        self.assertEqual(log.read_text().splitlines(), [
            f"{windows} test Core Tests -c Release", f"{windows} test Core Tests -c Release",
            f"{windows} format whitespace . --folder --verify-no-changes", f"{self.root} test windows/Core Tests"])
        self.assertEqual((config.work_dir / "ci-windows-core-core.log").read_text(), "A compatible .NET SDK was not found.\n")

    def test_a_folder_that_doesnt_exist_fails_the_stage_in_one_line(self):
        config = self.config(dir="Windows App")
        missing = config.require_path("windows.dir")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ci.run(config, "core"), 1)
        line = f"can't run dotnet in {missing}: no such folder"
        self.assertEqual(out.getvalue().splitlines(), ["::group::dotnet test Core Tests -c Release", line, "::endgroup::"])
        self.assertEqual((config.work_dir / "ci-windows-core-core.log").read_text(), line + "\n")
        with redirect_stdout(out):
            self.assertEqual(companion_ci.run(config, [(self.root, ["no-such-tool-here"])], "missing.log"), 1)
        logged = (config.work_dir / "missing.log").read_text()
        self.assertTrue(logged.startswith(f"can't run no-such-tool-here in {self.root}: "), logged)

    def test_an_app_without_a_windows_folder_is_told_so(self):
        config = self.config()
        config.data.pop("windows")
        with mock.patch.object(companion_ci, "load_config", return_value=config), self.assertRaises(SystemExit) as exited:
            ci.main(["format"])
        self.assertEqual(exited.exception.code, "ci-windows-core: companion.json needs windows.dir")

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
