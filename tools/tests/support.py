"""Shared by the tools tests: the fixture app, copied into a temporary folder so a test may change it, and how to run a
shell script on each OS."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parent
FIXTURE_APP = HERE / "fixture-app"
WINDOWS = os.name == "nt"

sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402


BASH = companion.git_bash() if WINDOWS else "bash"
"""The bash that runs the shell scripts; None on a Windows without Git for Windows."""

NO_SHELL = "Windows starts no shell script itself and has no Git for Windows bash here"
"""The reason a test that runs a shell script skips when `BASH` is None."""

NO_SHEBANG = "Windows can't start a `#!/bin/sh` file as a program, as this test's fake tool is"
"""The reason a test whose fake tool is a shell script skips on Windows."""


def shell(script: Path) -> list[str]:
    """The command that runs `script`: the script itself on macOS and Linux, through `BASH` on Windows."""
    return companion.shell_command(script) or [str(script)]


def search_path(*folders: Path) -> str:
    """$PATH with `folders` first (a test's fake commands), joined as this OS joins it."""
    return os.pathsep.join([*map(str, folders), os.environ["PATH"]])


@contextmanager
def fixture_app():
    """A fresh copy of the fixture app, with $COMPANION_ROOT pointing at it while the block runs."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve() / "app"
        shutil.copytree(FIXTURE_APP, root)
        previous = os.environ.get("COMPANION_ROOT")
        os.environ["COMPANION_ROOT"] = str(root)
        try:
            yield root
        finally:
            if previous is None:
                os.environ.pop("COMPANION_ROOT", None)
            else:
                os.environ["COMPANION_ROOT"] = previous


class FixtureAppTestCase(unittest.TestCase):
    """A test with a fresh copy of the fixture app at `self.root` ($COMPANION_ROOT while it runs), gone after it."""

    def setUp(self):
        app = fixture_app()
        self.root = app.__enter__()
        self.addCleanup(app.__exit__, None, None, None)


def git(cwd: Path, *args: str) -> str:
    """git in `cwd` with a throwaway identity, unsigned, `main` for a new repository and file:// submodules allowed,
    without the GIT_* variables of the git running these tests (a hook's GIT_DIR); its stdout, stripped. Raises on a
    failure."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    command = ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "-c", "init.defaultBranch=main",
               "-c", "tag.gpgSign=false", "-c", "commit.gpgSign=false", "-c", "protocol.file.allow=always", *args]
    return subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True, encoding="utf8").stdout.strip()


def quiet(function, *args):
    """`function(*args)` with its stdout captured: (its result, what it printed)."""
    out = io.StringIO()
    with redirect_stdout(out):
        result = function(*args)
    return result, out.getvalue()
