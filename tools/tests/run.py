#!/usr/bin/env python3
"""Runs every tools test (`test_*.py` here), standard library only, side by side in processes of their own.

    python3 tools/tests/run.py           # all of them
    python3 tools/tests/run.py l10n      # the ones whose file name contains "l10n"
    python3 tools/tests/run.py --jobs 1  # one process at a time (default: one per CPU)

Each file runs as two processes, one for its odd tests and one for its even ones, so its slow tests split between
them. A line per file as it ends, the failing files' output in full, then one summary. The tests share no state: each
works in its own temporary folders, and what one sets in os.environ stays in its process.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARTS = 2
"""The processes a file's tests are dealt across: 2 takes the whole run from about 30 s to about 23 s on a 10-core Mac,
more doesn't go faster (the tests' own git and python processes fill the CPUs)."""
COUNTS = ("run", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Runs the tools tests.")
    parser.add_argument("words", nargs="*", help="run only the files whose name contains one of these")
    parser.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 1, help="processes run at once")
    parser.add_argument("--part", nargs=3, metavar=("MODULE", "PART", "RESULTS"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.part:
        return run_part(args.part[0], int(args.part[1]), args.part[2])
    paths = [path for path in sorted(HERE.glob("test_*.py"))
             if not args.words or any(word in path.stem for word in args.words)]
    # The biggest files first: they are the slow ones, so none starts last and holds the run up.
    paths.sort(key=lambda path: path.stat().st_size, reverse=True)
    start = time.perf_counter()
    totals = dict.fromkeys(COUNTS, 0)
    left = dict.fromkeys((path.stem for path in paths), PARTS)
    outputs: dict[str, list[tuple[int, str]]] = {module: [] for module in left}
    failed: set[str] = set()
    with tempfile.TemporaryDirectory() as temp, ThreadPoolExecutor(max(1, args.jobs)) as pool:
        runs = [pool.submit(run_process, path.stem, part, Path(temp) / f"{path.stem}.{part}.json")
                for path in paths for part in range(PARTS)]
        for run in as_completed(runs):
            module, part, counts, output = run.result()
            if counts is None:  # the process died before it could report: one error standing for it
                counts = {**dict.fromkeys(COUNTS, 0), "run": 1, "errors": 1}
            for key in COUNTS:
                totals[key] += counts[key]
            outputs[module].append((part, output))
            if counts["failures"] or counts["errors"] or counts["unexpected_successes"]:
                failed.add(module)
            left[module] -= 1
            if not left[module]:
                print(f"{'FAIL' if module in failed else 'ok  '}  {module} ({time.perf_counter() - start:.1f} s)",
                      flush=True)
    for module in sorted(failed):
        print(f"\n{'=' * 70}\n{module}\n{'=' * 70}\n" + "\n".join(output.rstrip() for _, output in sorted(outputs[module])))
    print(f"{'-' * 70}\nRan {totals['run']} test{'' if totals['run'] == 1 else 's'} in {time.perf_counter() - start:.3f}s\n")
    details = [f"{key.replace('_', ' ')}={totals[key]}" for key in COUNTS[1:] if totals[key]]
    print(("FAILED" if failed else "OK") + (f" ({', '.join(details)})" if details else ""))
    return 1 if failed else 0


def run_process(module: str, part: int, results: Path) -> tuple[str, int, dict | None, str]:
    """Runs one part of a test file in a process of its own: the file, the part, its counts (None when the process
    didn't report them) and its output, prints and tracebacks in the order they came."""
    done = subprocess.run([sys.executable, __file__, "--part", module, str(part), str(results)],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf8",
                          errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
    if results.is_file():
        return module, part, json.loads(results.read_text("utf8")), done.stdout
    return module, part, None, done.stdout + f"\n{module} (part {part}) exited with {done.returncode} before reporting\n"


def run_part(module: str, part: int, results: str) -> int:
    """Runs every PARTS-th test of one file, from its `part`-th, in this process and writes the counts to `results`."""
    sys.path.insert(0, str(HERE.parent))
    sys.path.insert(0, str(HERE))
    try:
        tests = list(flatten(unittest.TestLoader().loadTestsFromName(module)))[part::PARTS]
    except unittest.SkipTest as skip:  # a file that can't run on this OS (test_vm on Windows)
        tests = [skipped(module, str(skip))] if part == 0 else []
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(tests))
    counts = (result.testsRun, len(result.failures), len(result.errors), len(result.skipped),
              len(result.expectedFailures), len(result.unexpectedSuccesses))
    Path(results).write_text(json.dumps(dict(zip(COUNTS, counts))), "utf8")
    return 0 if result.wasSuccessful() else 1


def flatten(suite: unittest.TestSuite):
    """The tests in `suite` and the suites inside it, in order (a class's setUpClass still runs once per process)."""
    for test in suite:
        yield from flatten(test) if isinstance(test, unittest.TestSuite) else (test,)


def skipped(module: str, reason: str) -> unittest.TestCase:
    """One skipped test standing for a test file that skipped itself as it loaded, so the run reports it and why."""
    case = type(module, (unittest.TestCase,), {"test_file": lambda self: self.skipTest(reason)})
    return case("test_file")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
