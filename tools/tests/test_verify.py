"""The shared verifier's failure, process, lock and changed-path behavior, without platform builds."""

import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from support import BASH, NO_SHELL, TOOLS, WINDOWS, git


@unittest.skipUnless(BASH, NO_SHELL)
class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GIT_", "LACHING_", "MBH_"))}
        self.env.update(LACHING_CHECK_SECONDS="12", LACHING_PROGRESS_SECONDS="1", LACHING_PROGRESS_INTERVAL="1")

    def start(self, body, *, env=None):
        script = self.root / f"verify-{time.monotonic_ns()}.sh"
        script.write_text('set -uo pipefail\nchanged=\"\" all=0\n'
                          f'. {shlex.quote(str(TOOLS / "verify-lib.sh"))}\n{body}\n', encoding="utf8")
        process = subprocess.Popen([BASH, str(script)], cwd=self.root, env={**self.env, **(env or {})},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)

    @staticmethod
    def kill_child(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def run_verify(self, body, *, env=None):
        process = self.start(body, env=env)
        out, err = process.communicate(timeout=20)
        return process.returncode, out, err

    def await_file(self, path):
        for _ in range(100):
            if path.exists() and path.read_text().strip():
                return path.read_text().strip()
            time.sleep(0.05)
        self.fail(f"Process never wrote {path}")

    def test_failures_are_logged_and_do_not_skip_following_checks(self):
        status, out, err = self.run_verify('check bad bash -c \'echo diagnostic; exit 7\'\n'
                                           'check good true\nskip "missing tool"\nsummary\nfinish')
        self.assertEqual(status, 1, out + err)
        self.assertIn("FAIL  bad\n      diagnostic\n", out)
        self.assertIn("ok    good\n", out)
        self.assertIn("skip  missing tool\n", out)
        self.assertIn("1 check(s) failed.", out)

    def test_timeout_reports_progress_and_continues(self):
        status, out, err = self.run_verify('check stuck sleep 30\ncheck next true\nfinish',
                                           env={"LACHING_CHECK_SECONDS": "2"})
        self.assertEqual(status, 1, out + err)
        self.assertIn("...   stuck, running", out)
        self.assertIn("stopped after 2 s", out)
        self.assertIn("ok    next", out)

    @unittest.skipIf(WINDOWS, "POSIX signal and process-group assertions")
    def test_timeout_kills_descendants_even_when_they_ignore_term(self):
        child = self.root / "child.py"
        child.write_text('import os, signal, time\nfrom pathlib import Path\n'
                         'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                         'Path("child.pid").write_text(str(os.getpid()))\ntime.sleep(30)\n')
        status, out, err = self.run_verify(f'check stuck {shlex.quote(sys.executable)} child.py\nfinish',
                                           env={"LACHING_CHECK_SECONDS": "2"})
        self.assertEqual(status, 1, out + err)
        self.assert_dead(int(self.await_file(self.root / "child.pid")))

    @unittest.skipIf(WINDOWS, "POSIX signal and process-group assertions")
    def test_signal_cleans_its_children_and_preserves_an_unrelated_process(self):
        unrelated = subprocess.Popen(["sleep", "30"])
        self.addCleanup(self.stop, unrelated)
        process = self.start('check waiting bash -c \'sleep 30 & echo $! > child.pid; wait\'\nfinish')
        child = int(self.await_file(self.root / "child.pid"))
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143, out + err)
        self.assert_dead(child)
        self.assertIsNone(unrelated.poll())

    @unittest.skipIf(WINDOWS, "POSIX process-group assertions")
    def test_success_cleans_up_background_children(self):
        status, out, err = self.run_verify('check done bash -c \'sleep 30 & echo $! > child.pid\'\nfinish')
        self.assertEqual(status, 0, out + err)
        self.assert_dead(int(self.await_file(self.root / "child.pid")))

    def assert_dead(self, pid):
        for _ in range(40):
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            if not state or state.startswith("Z"):
                return
            time.sleep(0.05)
        self.fail(f"Check left process {pid} running ({state})")

    def test_named_locks_serialize_and_release_after_failure(self):
        body = ('work() { echo start >> order; sleep 0.4; echo end >> order; }\n'
                'check build locked shared work\nfinish')
        first = self.start(body)
        second = self.start(body)
        for process in (first, second):
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual((self.root / "order").read_text().splitlines(), ["start", "end", "start", "end"])
        status, out, err = self.run_verify('check fail locked shared false\nfinish')
        self.assertEqual(status, 1, out + err)
        status, out, err = self.run_verify('check retry locked shared true\nfinish')
        self.assertEqual(status, 0, out + err)

    def test_stale_lock_metadata_cannot_block_a_build(self):
        folder = self.root / "build/verify-locks"
        folder.mkdir(parents=True)
        (folder / "shared.lock").write_text("999999999\n")
        status, out, err = self.run_verify('check build locked shared true\nfinish')
        self.assertEqual(status, 0, out + err)
        self.assertIn("ok    build", out)
        self.assertNotIn("999999999", (folder / "shared.lock").read_text())

    def test_a_crashed_lock_holder_releases_the_os_lock(self):
        folder = self.root / "build/verify-locks"
        folder.mkdir(parents=True)
        ready = self.root / "holder.ready"
        holder = subprocess.Popen([sys.executable, str(TOOLS / "verify-lock.py"),
                                   str(folder / "shared.lock"), str(ready), str(os.getpid())])
        self.addCleanup(self.stop, holder)
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "Lock holder did not acquire the lock")
        holder.kill()
        holder.wait(timeout=5)
        status, out, err = self.run_verify('check retry locked shared true\nfinish')
        self.assertEqual(status, 0, out + err)

    def test_timeout_releases_a_build_lock_for_the_next_run(self):
        status, out, err = self.run_verify('check stuck locked shared sleep 30\nfinish',
                                           env={"LACHING_CHECK_SECONDS": "2"})
        self.assertEqual(status, 1, out + err)
        status, out, err = self.run_verify('check retry locked shared true\nfinish')
        self.assertEqual(status, 0, out + err)

    @unittest.skipIf(WINDOWS, "POSIX signal and process-group assertions")
    def test_waiter_cannot_enter_before_term_resistant_build_stops(self):
        child = self.root / "resistant.py"
        child.write_text('import os, signal, time\nfrom pathlib import Path\n'
                         'signal.signal(signal.SIGTERM, lambda *_: Path("term.received").write_text("received"))\n'
                         'Path("resistant.pid").write_text(str(os.getpid()))\ntime.sleep(30)\n')
        observer = self.root / "observe.py"
        observer.write_text('import subprocess, sys\nfrom pathlib import Path\n'
                            'pid = Path("resistant.pid").read_text()\n'
                            'state = subprocess.run(["ps", "-o", "stat=", "-p", pid], '
                            'capture_output=True, text=True).stdout.strip()\n'
                            'Path("entered").write_text(state or "gone")\n'
                            'sys.exit(0 if not state or state.startswith("Z") else 1)\n')
        python = shlex.quote(sys.executable)
        for stopping, interrupt, expected in (("timeout", None, 1), ("TERM", signal.SIGTERM, 143),
                                              ("INT", signal.SIGINT, 130), ("repeated signals", signal.SIGTERM, 143)):
            with self.subTest(stopping=stopping):
                for name in ("resistant.pid", "term.received", "entered", "waiting"):
                    (self.root / name).unlink(missing_ok=True)
                first = self.start(f'check first locked shared {python} resistant.py\nfinish',
                                   env={"LACHING_CHECK_SECONDS": "2" if stopping == "timeout" else "12"})
                child_pid = int(self.await_file(self.root / "resistant.pid"))
                self.addCleanup(self.kill_child, child_pid)
                second = self.start(f'echo started > waiting\ncheck second locked shared {python} observe.py\nfinish')
                self.await_file(self.root / "waiting")
                if interrupt is not None:
                    time.sleep(0.2)  # Let the second verifier block on the held OS lock.
                    first.send_signal(interrupt)
                    if stopping == "repeated signals":
                        self.await_file(self.root / "term.received")
                        # Both arrive during the TERM-to-KILL grace period in EXIT cleanup.
                        first.send_signal(signal.SIGINT)
                        first.send_signal(signal.SIGTERM)
                out, err = first.communicate(timeout=10)
                self.assertEqual(first.returncode, expected, out + err)
                out, err = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 0, out + err)
                self.assertTrue((self.root / "entered").exists())
                self.assert_dead(child_pid)

    def test_invalid_lock_names_fail_without_running_the_command(self):
        status, out, err = self.run_verify('check invalid locked ../other touch ran\nfinish')
        self.assertEqual(status, 1, out + err)
        self.assertFalse((self.root / "ran").exists())

    def test_changed_paths_and_tools_preserve_spaces_and_absolute_paths(self):
        local = self.root / "landing/node_modules/.bin/local-tool"
        local.parent.mkdir(parents=True)
        local.write_text("#!/bin/sh\n")
        local.chmod(0o755)
        status, out, err = self.run_verify(
            'mode=paths base=""\npaths=("$PWD/path with spaces.md" "other.swift" "other.swift")\n'
            'discover_changes "$PWD"\nprintf "paths:\\n%s\\n" "$changed"\n'
            'find_tool local-tool "$PWD/landing"\nfinish')
        self.assertEqual(status, 0, out + err)
        self.assertIn("paths:\nother.swift\npath with spaces.md\n", out)
        self.assertIn(str(local), out)

    def test_worktree_staged_base_and_submodule_discovery_under_git_hooks(self):
        source = self.root / "source"
        source.mkdir()
        git(source, "init", "-q")
        (source / "page.ts").write_text("first\n")
        git(source, "add", "page.ts")
        git(source, "commit", "-qm", "first")
        git(self.root, "init", "-q")
        git(self.root, "submodule", "add", "-q", str(source), "landing")
        (self.root / "tracked.swift").write_text("first\n")
        git(self.root, "add", "landing", ".gitmodules", "tracked.swift")
        git(self.root, "commit", "-qm", "first")
        base = git(self.root, "rev-parse", "HEAD")
        landing = self.root / "landing"
        (landing / "page.ts").write_text("committed\n")
        git(landing, "add", "page.ts")
        git(landing, "commit", "-qm", "second")
        (landing / "untracked.ts").touch()
        (self.root / "tracked.swift").write_text("second\n")
        (self.root / "new file.swift").touch()
        hook = {"GIT_DIR": str(self.root / ".git"), "GIT_INDEX_FILE": str(self.root / ".git/index"),
                "GIT_WORK_TREE": str(self.root), "GIT_COMMON_DIR": str(self.root / ".git")}
        command = 'paths=()\ndiscover_changes "$PWD" landing\nprintf "paths:\\n%s\\n" "$changed"\nfinish'
        status, out, err = self.run_verify('mode=worktree base=""\n' + command, env=hook)
        self.assertEqual(status, 0, out + err)
        for path in ("landing/page.ts", "landing/untracked.ts", "tracked.swift", "new file.swift"):
            self.assertIn(path, out)
        git(self.root, "add", "landing", "tracked.swift")
        status, out, err = self.run_verify('mode=staged base=""\n' + command, env=hook)
        self.assertEqual(status, 0, out + err)
        self.assertIn("landing/page.ts", out)
        self.assertNotIn("new file.swift", out)
        git(self.root, "commit", "-qm", "second")
        status, out, err = self.run_verify(f'mode=base base={base}\n' + command, env=hook)
        self.assertEqual(status, 0, out + err)
        self.assertIn("landing/page.ts", out)
        self.assertIn("new file.swift", out)


if __name__ == "__main__":
    unittest.main()
