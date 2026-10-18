"""covering.py's map from changed files to the tests that cover them, run.py's choice of Python, and verify.sh --fast
running only the covering tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import BASH, NO_SHEBANG, NO_SHELL, REPO, WINDOWS, git

import covering  # noqa: E402
import run  # noqa: E402

HERE = Path(__file__).resolve().parent


class CoveringTests(unittest.TestCase):
    def test_a_tool_runs_its_own_tests_and_the_shared_files_check(self):
        self.assertEqual(covering.covering(["tools/l10n.py"]), ["test_l10n", "test_scripts.SharedFilesTests"])
        self.assertEqual(covering.covering(["tools/windows/vm.py"]), ["test_scripts.SharedFilesTests", "test_vm"])
        self.assertEqual(covering.covering(["tools/store/push.py", "tools/store/listing.py"]),
                         ["test_push_microsoft", "test_scripts.SharedFilesTests", "test_store"])
        self.assertEqual(covering.covering(["tools/windows/assets.py"]), ["test_scripts.SharedFilesTests", "test_windows"])
        self.assertEqual(covering.covering(["tools/landing/src/index.ts"]), ["test_scripts.SharedFilesTests"])
        self.assertEqual(covering.covering(["Tests/Fixtures/characters/fixture/body.svg"]), ["test_character"])

    def test_a_test_file_covers_itself_and_the_whole_module_wins(self):
        self.assertEqual(covering.covering(["tools/tests/test_vm.py"]), ["test_scripts.SharedFilesTests", "test_vm"])
        self.assertEqual(covering.covering(["tools/tests/test_scripts.py", "tools/l10n.py"]), ["test_l10n", "test_scripts"])

    def test_a_shared_or_unknown_file_runs_them_all(self):
        for path in ("tools/companion.py", "tools/companion.schema.json", "tools/verify-lib.sh", "tools/tests/run.py",
                     "tools/tests/support.py", "tools/tests/fixture-app/companion.json", "tools/new-tool.py",
                     "tools/tests/helpers.py"):
            self.assertIs(covering.covering(["tools/l10n.py", path]), covering.ALL, path)

    def test_every_tracked_tools_file_is_mapped_or_shared(self):
        listed = subprocess.run(["git", "ls-files", "--", "tools", "Tests/Fixtures"], cwd=REPO, capture_output=True,
                                encoding="utf8")
        if listed.returncode:
            self.skipTest("not a git checkout")
        for path in listed.stdout.splitlines():
            shared = any(covering.fnmatchcase(path, pattern) for pattern in covering.SHARED)
            self.assertEqual(covering.covering([path]) is covering.ALL, shared,
                             f"{path}: map it in covering.COVERS, or list it in covering.SHARED")

    def test_every_named_test_exists(self):
        names = {name for tests in covering.COVERS.values() for name in tests} | set(covering.EVERY)
        for name in sorted(names):
            module, _, case = name.partition(".")
            path = HERE / f"{module}.py"
            self.assertTrue(path.is_file(), name)
            if case:
                self.assertIn(f"class {case}(", path.read_text("utf8"), name)


class PythonTests(unittest.TestCase):
    def test_a_new_enough_python_runs_on(self):
        with mock.patch.object(run.os, "execv") as execv:
            run.need_python((3, 10, 0), find=lambda: self.fail("no search on 3.10"))
            run.need_python((3, 14, 8))
        execv.assert_not_called()

    def test_an_old_python_reruns_under_a_newer_one(self):
        replaced = RuntimeError("execv replaces the process")
        with mock.patch.object(run.os, "execv", side_effect=replaced) as execv, \
                mock.patch.object(run.sys, "argv", ["run.py", "l10n"]), self.assertRaises(RuntimeError):
            run.need_python((3, 9, 6), find=lambda: "/opt/homebrew/bin/python3")
        execv.assert_called_once_with("/opt/homebrew/bin/python3",
                                      ["/opt/homebrew/bin/python3", str(HERE / "run.py"), "l10n"])

    def test_an_old_python_without_a_newer_one_stops_with_one_line(self):
        with self.assertRaises(SystemExit) as stop:
            run.need_python((3, 9, 6), find=lambda: None)
        message = str(stop.exception.code)
        self.assertNotIn("\n", message)
        self.assertIn("need Python 3.10 or later", message)
        self.assertIn("is 3.9.6", message)

    @unittest.skipIf(WINDOWS, NO_SHEBANG)
    def test_the_search_takes_the_first_new_enough_python3(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new, also = (Path(temp) / name for name in ("old", "new", "also"))
            for folder, status in ((old, 1), (new, 0), (also, 0)):
                folder.mkdir()
                (folder / "python3").write_text(f"#!/bin/sh\nexit {status}\n", "utf8")
                (folder / "python3").chmod(0o755)
            empty = str(Path(temp) / "missing")
            self.assertEqual(run.newer_python(os.pathsep.join([empty, str(old), str(new)]), (str(also),)),
                             str(new / "python3"))
            self.assertEqual(run.newer_python(str(old), (str(also),)), str(also / "python3"), "then the folders")
            self.assertIsNone(run.newer_python(str(old), ()))


# A test file for the copy's run.py: each test appends its name to $MARKS when it runs.
FAKE_TEST = '''import os, unittest


class {name}(unittest.TestCase):
    def test_it(self):
        with open(os.environ["MARKS"], "a") as f:
            f.write("{module}.{name}\\n")
'''


@unittest.skipIf(BASH is None, NO_SHELL)
class VerifyFastTests(unittest.TestCase):
    """scripts/verify.sh in a copy of the repository whose tools tests are fakes that say they ran."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "laching"
        for name in ("scripts/verify.sh", "tools/verify-lib.sh", "tools/tests/run.py", "tools/tests/covering.py",
                     "tools/l10n.py", "tools/windows/vm.py", "Sources/LachingCore/Laching.swift", "kotlin/gradle.properties",
                     "README.md", "site/index.html", "dotnet/Directory.Build.props", "web/Directory.Build.props"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, self.root / name)
        (self.root / "scripts/fixture-art.sh").write_text("#!/usr/bin/env bash\nexit 0\n", "utf8")
        (self.root / "scripts/fixture-art.sh").chmod(0o755)
        for module, name in (("test_l10n", "L10nTests"), ("test_vm", "VmTests"), ("test_scripts", "ScriptTests"),
                             ("test_scripts", "SharedFilesTests")):
            with (self.root / "tools/tests" / f"{module}.py").open("a", encoding="utf8") as f:
                f.write(FAKE_TEST.format(module=module, name=name))
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "copy")
        self.marks = Path(temporary.name) / "marks"

    def verify(self, *args: str) -> tuple[str, list[str]]:
        self.marks.unlink(missing_ok=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        done = subprocess.run([BASH, "scripts/verify.sh", *args], cwd=self.root, capture_output=True, encoding="utf8",
                              env={**env, "MARKS": str(self.marks)})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ran = self.marks.read_text("utf8").split() if self.marks.exists() else []
        return done.stdout, sorted(ran)

    def test_fast_runs_the_tests_that_cover_the_change(self):
        with (self.root / "tools/l10n.py").open("a", encoding="utf8") as f:
            f.write("# changed\n")
        out, ran = self.verify("--fast")
        self.assertIn("ok    tools tests (test_l10n test_scripts.SharedFilesTests)", out)
        self.assertEqual(ran, ["test_l10n.L10nTests", "test_scripts.SharedFilesTests"])
        out, ran = self.verify()
        self.assertIn("ok    tools tests\n", out, "without --fast, every test")
        self.assertEqual(len(ran), 4)

    def test_fast_runs_them_all_for_a_shared_file_or_a_deleted_one(self):
        with (self.root / "tools/verify-lib.sh").open("a", encoding="utf8") as f:
            f.write("# changed\n")
        self.assertEqual(len(self.verify("--fast")[1]), 4)
        git(self.root, "checkout", "--", "tools/verify-lib.sh")
        (self.root / "tools/windows/vm.py").unlink()
        self.assertEqual(self.verify("--fast")[1], ["test_scripts.SharedFilesTests", "test_vm.VmTests"])


if __name__ == "__main__":
    unittest.main()
