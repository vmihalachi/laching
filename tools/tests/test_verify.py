"""The shared verifier's failure, process, lock and changed-path behavior, without platform builds."""

import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from support import BASH, NO_SHELL, TOOLS, WINDOWS, git


def own(err):
    """A verifier's stderr without the line Bash 3.2 can print on a busy Mac when it starts a job: the job and the
    shell both set its process group, and the one that comes second is refused. The job has its group either way."""
    return re.sub(r"^.*: child setpgid \(\d+ to \d+\): Operation not permitted\n", "", err, flags=re.M)


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
        self.script = script
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
        return process.returncode, out, own(err)

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

    # --- queued checks: the groups side by side, the checks of one group in sequence ---

    MEET = ('meet() { touch "$1.here"; for _ in $(seq 100); do [ ! -f "$2.here" ] || return 0; sleep 0.05; done; '
            'echo "never met $2"; return 1; }\n')
    """A stub check that passes only while the other one runs: each waits for the other's file."""
    WORK = 'work() { echo "start $1" >> order; sleep 0.3; echo "end $1" >> order; }\n'
    QUIET = {"LACHING_PROGRESS_SECONDS": "9"}
    """No progress line for a check of a second or two: the output compared is the checks' own."""

    def assert_no_runner(self):
        """No process of the last verifier's script is left: neither it nor a group's runner (a fork of it)."""
        for _ in range(40):
            listed = subprocess.run(["ps", "-axo", "pid=,stat=,command="], capture_output=True, text=True).stdout
            left = [line for line in listed.splitlines()
                    if str(self.script) in line and not line.split()[1].startswith("Z")]
            if not left:
                return
            time.sleep(0.05)
        self.fail(f"The verifier left processes: {left}")

    @unittest.skipIf(WINDOWS, "Windows runs the queued checks in sequence")
    def test_queued_groups_run_side_by_side_and_report_in_the_order_queued(self):
        # Each of the first two passes only while the other runs; the first ends last and is still reported first.
        body = (self.MEET + 'queue one "first" bash -c \'while [ ! -f second.done ]; do sleep 0.05; done; echo late\'\n'
                'queue two "second" touch second.done\n'
                'queue three "third" meet a b\nqueue four "fourth" meet b a\nrun_queued\nfinish')
        status, out, err = self.run_verify(body, env=self.QUIET)
        self.assertEqual((status, out, err), (0, "ok    first\nok    second\nok    third\nok    fourth\n", ""))

    def test_queued_checks_of_one_group_stay_in_sequence(self):
        body = (self.WORK + 'queue build "one" work one\nqueue other "beside" sleep 0.1\nqueue build "two" work two\n'
                'run_queued\nfinish')
        status, out, err = self.run_verify(body, env=self.QUIET)
        self.assertEqual((status, out, err), (0, "ok    one\nok    beside\nok    two\n", ""))
        self.assertEqual((self.root / "order").read_text().splitlines(), ["start one", "end one", "start two", "end two"])

    def test_two_failing_queued_checks_are_both_reported_as_a_serial_run_reports_them(self):
        body = ('queue a "bad one" bash -c \'echo first diagnostic; exit 3\'\nqueue b "good" true\n'
                'queue c "bad two" bash -c \'echo second diagnostic; exit 4\'\nqueue a "after the first" true\n'
                'skip "missing tool"\nrun_queued\nsummary\nfinish')
        expected = ("FAIL  bad one\n      first diagnostic\nok    good\nFAIL  bad two\n      second diagnostic\n"
                    "ok    after the first\nskip  missing tool\nNo changed paths.\n2 check(s) failed.\n")
        for serial in ("0", "1"):
            with self.subTest(serial=serial):
                status, out, err = self.run_verify(body, env={**self.QUIET, "LACHING_VERIFY_SERIAL": serial})
                self.assertEqual((status, out, err), (1, expected, ""))

    def test_the_caller_hears_of_each_queued_check_in_order_with_its_result(self):
        # What verify.sh notes for the Stop hook: only a check that passed, with when it started. One that says it
        # skipped itself (the status it was queued under) is a skip line and no failure; the same status from a check
        # queued without it is a failure.
        body = ('heard() { echo "${queue_name[$1]}: ${queue_result[$1]} ${queue_started[$1]}" >> heard; }\n'
                'queue_after=heard\nqueue a "passes" true\nqueue b "fails" false\nverify_skip_status=77\n'
                'queue c "skips itself" bash -c \'echo needs a tool; exit 77\'\nverify_skip_status=""\n'
                'queue c "no skip" bash -c \'exit 77\'\nqueue a "passes too" true\nrun_queued\nsummary\nfinish')
        for serial in ("0", "1"):
            with self.subTest(serial=serial):
                (self.root / "heard").unlink(missing_ok=True)
                before = int(time.time())
                status, out, err = self.run_verify(body, env={**self.QUIET, "LACHING_VERIFY_SERIAL": serial})
                self.assertEqual((status, err), (1, ""), out)
                self.assertEqual(out, "ok    passes\nFAIL  fails\nFAIL  no skip\nok    passes too\n"
                                      "skip  skips itself: needs a tool\nNo changed paths.\n2 check(s) failed.\n")
                heard = [line.rsplit(" ", 2) for line in (self.root / "heard").read_text().splitlines()]
                self.assertEqual([line[:2] for line in heard], [["passes:", "ok"], ["fails:", "fail"],
                                                                ["skips itself:", "skip"], ["no skip:", "fail"],
                                                                ["passes too:", "ok"]])
                for _, _, started in heard:
                    self.assertTrue(before <= int(started) <= int(time.time()), heard)

    def test_run_queued_runs_what_was_queued_since_the_last_call(self):
        body = (self.WORK + 'queue core "one" work one\nrun_queued\nqueue a "two" work two\nqueue b "three" true\n'
                'run_queued\nrun_queued\nfinish')
        status, out, err = self.run_verify(body, env=self.QUIET)
        self.assertEqual((status, out, err), (0, "ok    one\nok    two\nok    three\n", ""))
        self.assertEqual((self.root / "order").read_text().splitlines(), ["start one", "end one", "start two", "end two"])

    def test_serial_runs_the_groups_one_after_another(self):
        # LACHING_VERIFY_SERIAL, and Windows (Git Bash) unless it says 0.
        body = self.WORK + 'queue a "one" work one\nqueue b "two" work two\nrun_queued\nfinish'
        for case, start, env in (("asked", "", {"LACHING_VERIFY_SERIAL": "1"}), ("Windows", "OSTYPE=msys\n", {})):
            with self.subTest(case=case):
                (self.root / "order").unlink(missing_ok=True)
                status, out, err = self.run_verify(start + body, env={**self.QUIET, **env})
                self.assertEqual((status, out, err), (0, "ok    one\nok    two\n", ""))
                self.assertEqual((self.root / "order").read_text().splitlines(),
                                 ["start one", "end one", "start two", "end two"])

    @unittest.skipIf(WINDOWS, "Windows runs the queued checks in sequence")
    def test_windows_runs_side_by_side_when_asked(self):
        body = 'OSTYPE=msys\n' + self.MEET + 'queue a "one" meet a b\nqueue b "two" meet b a\nrun_queued\nfinish'
        status, out, err = self.run_verify(body, env={**self.QUIET, "LACHING_VERIFY_SERIAL": "0"})
        self.assertEqual((status, out, err), (0, "ok    one\nok    two\n", ""))

    @unittest.skipIf(WINDOWS, "Windows runs the queued checks in sequence")
    def test_a_queued_check_reports_progress_and_its_timeout(self):
        status, out, err = self.run_verify('queue a "stuck" sleep 30\nqueue b "next" true\nrun_queued\nfinish',
                                           env={"LACHING_CHECK_SECONDS": "2"})
        self.assertEqual((status, err), (1, ""), out)
        self.assertRegex(out, r"^\.\.\.   stuck, running 0 min [12] s \(since \d\d:\d\d\)\n")
        self.assertIn("FAIL  stuck: stopped after 2 s (LACHING_CHECK_SECONDS=2); the end of its log:\nok    next\n", out)

    @unittest.skipIf(WINDOWS, "Windows runs the queued checks in sequence")
    def test_queued_checks_share_a_named_lock_with_another_verifier(self):
        body = self.WORK + 'queue a "build" locked shared work "$1"\nqueue b "beside" true\nrun_queued\nfinish'
        first = self.start(body.replace('"$1"', "first"), env=self.QUIET)
        second = self.start(body.replace('"$1"', "second"), env=self.QUIET)
        for process in (first, second):
            out, err = process.communicate(timeout=15)
            self.assertEqual((process.returncode, out, own(err)), (0, "ok    build\nok    beside\n", ""))
        order = (self.root / "order").read_text().splitlines()
        self.assertEqual([line.split()[0] for line in order], ["start", "end", "start", "end"], order)

    @unittest.skipIf(WINDOWS, "POSIX signal and process-group assertions")
    def test_an_interrupted_queued_run_leaves_no_stray_processes(self):
        # Two groups mid-check, one under a build lock with a child that ignores TERM: the signal ends the checks,
        # their descendants and the groups' runners, and the lock is free for the next run.
        child = self.root / "resistant.py"
        child.write_text('import os, signal, time\nfrom pathlib import Path\n'
                         'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                         'Path("resistant.pid").write_text(str(os.getpid()))\ntime.sleep(30)\n')
        body = (f'queue a "first" locked shared {shlex.quote(sys.executable)} resistant.py\n'
                'queue b "second" bash -c \'sleep 30 & echo $! > child.pid; wait\'\n'
                'queue a "never starts" touch started\nrun_queued\nfinish')
        for interrupt, expected in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
            with self.subTest(interrupt=interrupt.name):
                for name in ("resistant.pid", "child.pid"):
                    (self.root / name).unlink(missing_ok=True)
                unrelated = subprocess.Popen(["sleep", "30"])
                self.addCleanup(self.stop, unrelated)
                process = self.start(body, env=self.QUIET)
                children = [int(self.await_file(self.root / name)) for name in ("resistant.pid", "child.pid")]
                for pid in children:
                    self.addCleanup(self.kill_child, pid)
                process.send_signal(interrupt)
                out, err = process.communicate(timeout=10)
                self.assertEqual((process.returncode, out, own(err)), (expected, "", ""))
                for pid in children:
                    self.assert_dead(pid)
                self.assert_no_runner()
                self.assertFalse((self.root / "started").exists())
                self.assertIsNone(unrelated.poll())
                status, out, err = self.run_verify('check retry locked shared true\nfinish', env=self.QUIET)
                self.assertEqual((status, out, err), (0, "ok    retry\n", ""))

    @unittest.skipIf(WINDOWS, "POSIX signal and process-group assertions")
    def test_queued_checks_stop_when_their_verifier_is_killed(self):
        # KILL runs no cleanup in the verifier: each group's runner sees it gone and stops its own check.
        body = ('queue a "first" bash -c \'sleep 30 & echo $! > one.pid; wait\'\n'
                'queue b "second" bash -c \'sleep 30 & echo $! > two.pid; wait\'\nrun_queued\nfinish')
        process = self.start(body)
        children = [int(self.await_file(self.root / name)) for name in ("one.pid", "two.pid")]
        for pid in children:
            self.addCleanup(self.kill_child, pid)
        process.kill()  # a zombie until communicate() reaps it, which waits for the runners to let go of its output
        process.communicate(timeout=10)
        for pid in children:
            self.assert_dead(pid)
        self.assert_no_runner()

    @unittest.skipIf(WINDOWS, "POSIX signal and process-group assertions")
    def test_a_group_whose_runner_dies_fails_its_checks(self):
        # Never a silent pass: the check it was running and the ones it never reached are failures, the other
        # group's result stands. The verifier stops the check its runner no longer can.
        body = ('queue a "passes first" true\n'
                'queue a "doomed" bash -c \'echo last words; sleep 30 & echo $! > child.pid; kill -KILL $PPID; wait\'\n'
                'queue b "other group" true\nqueue a "never reached" touch reached\nrun_queued\nsummary\nfinish')
        status, out, err = self.run_verify(body, env=self.QUIET)
        self.assertEqual(status, 1, out + err)
        self.assertRegex(err, r"\A(.*Killed.*\n)?\Z")  # Bash's own word on the runner
        self.assert_dead(int((self.root / "child.pid").read_text()))
        self.assertEqual(out, "ok    passes first\n"
                              "FAIL  doomed: its run stopped without a result; the end of its log:\n      last words\n"
                              "ok    other group\nFAIL  never reached: never ran, the run of its group stopped\n"
                              "No changed paths.\n2 check(s) failed.\n")
        self.assertFalse((self.root / "reached").exists())

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
