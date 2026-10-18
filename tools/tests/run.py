#!/usr/bin/env python3
"""Runs every tools test (`test_*.py` here), standard library only.

    python3 tools/tests/run.py                          # all of them
    python3 tools/tests/run.py l10n                     # the ones whose file name contains "l10n"
    python3 tools/tests/run.py test_scripts.SharedFilesTests   # a dotted name: that module's class or test
    python3 tools/tests/run.py --covering PATH...       # prints the tests that cover PATH... (covering.py), or "all"

The tests need Python 3.10 or later: an older python3 (macOS's /usr/bin/python3 is 3.9) re-runs this file under a newer
one on PATH, in /opt/homebrew/bin or /usr/local/bin, or stops with one line when there's none.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MINIMUM = (3, 10)
FOLDERS = ("/opt/homebrew/bin", "/usr/local/bin")


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(HERE.parent))
    sys.path.insert(0, str(HERE))
    if argv[:1] == ["--covering"]:
        from covering import covering

        names = covering(argv[1:])
        print("all" if names is None else " ".join(names))
        return 0
    need_python()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in (word for word in argv if "." in word):
        suite.addTests(loader.loadTestsFromName(name))
    words = [word for word in argv if "." not in word]
    for path in sorted(HERE.glob("test_*.py")):
        if argv and not any(word in path.stem for word in words):
            continue
        try:
            suite.addTests(loader.loadTestsFromName(path.stem))
        except unittest.SkipTest as skip:  # a file that can't run on this OS (test_vm on Windows)
            suite.addTest(skipped(path.stem, str(skip)))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def skipped(module: str, reason: str) -> unittest.TestCase:
    """One skipped test standing for a test file that skipped itself as it loaded, so the run reports it and why."""
    case = type(module, (unittest.TestCase,), {"test_file": lambda self: self.skipTest(reason)})
    return case("test_file")


def need_python(version: tuple = sys.version_info, find=None) -> None:
    """Re-runs this file under a newer python3 when this one is older than MINIMUM, else stops with one line."""
    if tuple(version[:2]) >= MINIMUM:
        return
    newer = (find or newer_python)()
    if newer:
        os.execv(newer, [newer, str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(f"tools/tests/run.py: the tools tests need Python {'.'.join(map(str, MINIMUM))} or later, and "
             f"{sys.executable} is {'.'.join(map(str, version[:3]))}, with no newer python3 on PATH or in "
             f"{' or '.join(FOLDERS)} (brew install python)")


def newer_python(search: str | None = None, folders: tuple[str, ...] | None = None) -> str | None:
    """The first python3 of MINIMUM or later on `search` (default $PATH), then in `folders` (default FOLDERS), other
    than this one."""
    path = os.environ.get("PATH", "") if search is None else search
    seen = {os.path.realpath(sys.executable)}
    for folder in [*path.split(os.pathsep), *(FOLDERS if folders is None else folders)]:
        candidate = os.path.join(folder, "python3")
        real = os.path.realpath(candidate)
        if not folder or real in seen or not os.access(candidate, os.X_OK) or os.path.isdir(candidate):
            continue
        seen.add(real)
        probe = f"import sys; sys.exit(sys.version_info[:2] < {MINIMUM})"
        try:
            if subprocess.run([candidate, "-c", probe], capture_output=True, timeout=30).returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
