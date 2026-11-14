#!/usr/bin/env python3
"""Tests for Laching's own Git hooks (.githooks, enabled once per clone with `git config core.hooksPath .githooks`):
the pre-commit hook runs scripts/verify.sh's fast checks on what is staged, and a failure stops the commit."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import NO_SHEBANG, REPO, WINDOWS, git

HOOK = REPO / ".githooks" / "pre-commit"
"""The hook under test."""


class PreCommitTests(unittest.TestCase):
    def test_the_hook_is_a_program(self):
        self.assertTrue(HOOK.is_file(), HOOK)
        if not WINDOWS:
            self.assertTrue(os.access(HOOK, os.X_OK), "git runs only an executable hook")

    @unittest.skipIf(WINDOWS, NO_SHEBANG)
    def test_it_runs_the_fast_checks_on_the_staged_files_and_a_failure_stops_the_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            # A repository with the hook and a scripts/verify.sh that notes its arguments, exiting $FAKE_VERIFY_STATUS.
            repo, log = Path(temp).resolve() / "laching", Path(temp).resolve() / "verify.log"
            (repo / ".githooks").mkdir(parents=True)
            shutil.copy2(HOOK, repo / ".githooks" / "pre-commit")
            (repo / "scripts").mkdir()
            verify = repo / "scripts" / "verify.sh"
            verify.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexit "${{FAKE_VERIFY_STATUS:-0}}"\n', "utf8")
            verify.chmod(0o755)
            git(repo, "init", "-q")
            git(repo, "config", "core.hooksPath", ".githooks")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "first")
            self.assertEqual(log.read_text("utf8"), "--fast --staged\n")
            (repo / "README.md").write_text("Changed.\n", "utf8")
            git(repo, "add", "README.md")
            os.environ["FAKE_VERIFY_STATUS"] = "1"
            try:
                with self.assertRaises(subprocess.CalledProcessError):
                    git(repo, "commit", "-q", "-m", "second")
            finally:
                del os.environ["FAKE_VERIFY_STATUS"]
            self.assertEqual(git(repo, "rev-list", "--count", "HEAD"), "1", "the failed checks stopped the commit")
            self.assertEqual(log.read_text("utf8"), "--fast --staged\n" * 2)


if __name__ == "__main__":
    unittest.main()
