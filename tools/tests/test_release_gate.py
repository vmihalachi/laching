"""release-gate.py: a Mac or iPhone and iPad release goes ahead only on a commit the app's macos-app.yml caller passed
on, with no recorded skip over what ships. Over the fixture app made a git repository with an origin here; `gh` and the
agent hook's `skips --json` are stood in for, so nothing reaches GitHub."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from support import FixtureAppTestCase, git  # noqa: E402

spec = importlib.util.spec_from_file_location("release_gate", TOOLS / "release-gate.py")
release_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_gate)

# The agent hook's `skips --json` as the fake prints it: skips.json beside it, else nothing (a hook without the command
# fails open: exit 0, no output). It leaves the folders it was told the app is in, in env.json.
FAKE_HOOK = """import json, os, pathlib, sys
here = pathlib.Path(__file__).parent
here.joinpath("env.json").write_text(json.dumps({key: os.environ.get(key)
                                                 for key in ("CLAUDE_PROJECT_DIR", "COMPANION_ROOT")}))
if sys.argv[1:] == ["skips", "--json"] and here.joinpath("skips.json").is_file():
    sys.stdout.write(here.joinpath("skips.json").read_text("utf8"))
"""

RELEASE_WORKFLOW = """name: macOS
on:
  workflow_dispatch:
    inputs:
      hosted:
        type: boolean
        default: false
jobs:
  test:
    uses: vmihalachi/laching/.github/workflows/macos-app.yml@v0.5.0
    with:
      hosted: ${{ inputs.hosted }}
      release: true
"""

NO_RELEASE_WORKFLOW = RELEASE_WORKFLOW.replace("release: true", "release: false")
"""A caller that turns the Release builds off, so no run of it launches the Release app."""

HELPER_JOB = """  helper:
    uses: vmihalachi/laching/.github/workflows/macos-app.yml@v0.5.0
    with:
      hosted: ${{ inputs.hosted }}
      release: false  # a helper target: no Release app
"""
"""A second job calling macos-app.yml, with the Release builds off: the release job's own `release` still counts."""

FORWARDED_RELEASE_WORKFLOW = RELEASE_WORKFLOW.replace("""        default: false
jobs:""", """        default: false
      release:
        type: boolean
        default: false
jobs:""").replace("release: true", "release: ${{ inputs.release }}")
"""A caller that forwards its own `release` input, off unless a dispatch sets it."""


class ReleaseGateTests(FixtureAppTestCase):
    """The fixture app committed on `main`, its origin a bare repository beside it. `self.runs` is what `gh run list`
    answers, `self.steps` each run's step conclusions for `gh run view` (a run not in it launched the Release app, as a
    hosted one does), `self.skips` what the hook lists (None: the hook prints nothing) and `self.calls` every gh
    command. The fixture's caller forwards `hosted` and leaves `release` on, as macos-app.yml has it by default."""

    def setUp(self):
        super().setUp()
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "Release 1.0 (4)")
        origin = self.root.parent / "origin.git"
        git(self.root.parent, "init", "-q", "--bare", str(origin))
        git(self.root, "remote", "add", "origin", str(origin))
        self.sha = git(self.root, "rev-parse", "HEAD")
        self.runs: list[dict] = []
        self.steps: dict[int, dict[str, str]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.dispatch_out = "https://github.com/owner/app/actions/runs/77\n"  # what `gh workflow run` prints
        self.skips: list[dict] | None = []
        hook = self.root.parent / "hook" / "hook.py"
        hook.parent.mkdir()
        hook.write_text(FAKE_HOOK, "utf8")
        self.hook = hook
        for patch in (mock.patch.object(release_gate, "HOOK", hook),
                      mock.patch.object(release_gate, "gh", side_effect=self.gh)):
            patch.start()
            self.addCleanup(patch.stop)

    def gh(self, root: Path, *args: str) -> str:
        self.assertEqual(Path(root), self.root, "gh runs in the app, so it asks the app's repository")
        self.calls.append(args)
        if args[:2] == ("run", "list"):
            return json.dumps(self.runs)
        if args[:2] == ("run", "view"):
            steps = self.steps.get(int(args[2]), {release_gate.SMOKE_STEP: "success"})
            return json.dumps({"jobs": [{"name": "test / test", "steps": [
                {"name": name, "conclusion": conclusion} for name, conclusion in steps.items()]}]})
        if args[:2] == ("workflow", "run"):
            return self.dispatch_out
        self.fail(f"unexpected gh {args}")

    def run_(self, number: int, status: str = "completed", conclusion: str = "success", sha: str | None = None,
             minute: int | None = None) -> dict:
        """A run as `gh run list --json` gives it, created at `minute` past ten (its number by default)."""
        return {"databaseId": number, "status": status, "conclusion": conclusion if status == "completed" else "",
                "headSha": sha or self.sha, "event": "push",
                "url": f"https://github.com/owner/app/actions/runs/{number}",
                "createdAt": f"2026-11-14T10:{number if minute is None else minute:02d}:00Z"}

    def commit(self, message: str) -> None:
        """Commits every change of the app, which is then the commit gated."""
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        self.sha = git(self.root, "rev-parse", "HEAD")

    def release_builds(self, workflow: str = RELEASE_WORKFLOW) -> None:
        """The macOS caller says it builds the Release apps (by default): the Mac gate needs the hosted Release
        launch."""
        (self.root / ".github/workflows/macos.yml").write_text(workflow, "utf8")
        self.commit("Release builds" if workflow == RELEASE_WORKFLOW else "No Release builds")

    def with_ios(self) -> None:
        """The app has an iPhone and iPad scheme, so it has an iOS release to gate."""
        manifest = self.root / "companion.json"
        config = json.loads(manifest.read_text("utf8"))
        config["mac"]["iosScheme"] = "Fixture iOS"
        manifest.write_text(json.dumps(config, indent=2) + "\n", "utf8")
        self.commit("iOS")

    def gate(self, *argv: str) -> tuple[int, str]:
        (self.hook.parent / "skips.json").unlink(missing_ok=True)
        if self.skips is not None:
            (self.hook.parent / "skips.json").write_text(json.dumps(self.skips), "utf8")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            try:
                code = release_gate.main(list(argv))
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 2
        return code, out.getvalue()

    def push(self) -> None:
        git(self.root, "push", "-q", "origin", "main")

    def dispatched(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:2] == ("workflow", "run")]

    # --- the run on the exact commit ----------------------------------------------------------------------------------

    def test_a_green_run_on_the_exact_commit_lets_the_release_go(self):
        self.runs = [self.run_(10)]
        code, out = self.gate("mac")
        self.assertEqual(code, 0, out)
        self.assertIn("run 10 passed", out)
        listed = [call for call in self.calls if call[:2] == ("run", "list")]
        self.assertEqual(len(listed), 1)
        self.assertIn("--commit", listed[0])
        self.assertEqual(listed[0][listed[0].index("--commit") + 1], self.sha, "the full SHA of HEAD, nothing looser")
        self.assertEqual(listed[0][listed[0].index("--workflow") + 1], "macos.yml", "the app's macos-app.yml caller")

    def test_a_run_on_another_commit_counts_for_nothing(self):
        self.runs = [self.run_(10, sha="0" * 40)]
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn(f"no macos.yml run on {self.sha[:12]}", out)
        self.assertIn("--dispatch", out)
        self.assertEqual(self.dispatched(), [], "without --dispatch nothing starts")

    def test_a_failed_run_refuses_and_says_how_to_rerun_it(self):
        self.runs = [self.run_(12, conclusion="failure")]
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertIn("run 12 failure", out)
        self.assertIn("gh run rerun 12 --failed", out)
        self.assertEqual(self.dispatched(), [], "a red run is fixed or rerun, not papered over with a new one")

    def test_a_green_run_wins_over_a_red_one_on_the_same_commit(self):
        self.runs = [self.run_(13, conclusion="failure"), self.run_(11)]
        self.assertEqual(self.gate("mac")[0], 0)

    def test_the_newest_run_that_ended_decides_what_clears_it(self):
        self.release_builds()
        self.runs = [self.run_(60, conclusion="failure", minute=1), self.run_(61, minute=2)]
        self.steps[61] = {release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn("run 61 passed", out, "the newer green run, not the older red one")
        self.assertIn("-f hosted=true", out)
        self.assertNotIn("gh run rerun", out)
        self.runs = [self.run_(61, minute=2), self.run_(62, conclusion="failure", minute=3)]
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn("gh run rerun 62 --failed", out, "a newer red run after the green one without the launch")

    def test_a_cancelled_run_is_started_again_not_rerun(self):
        self.runs = [self.run_(63, conclusion="failure", minute=1), self.run_(64, conclusion="cancelled", minute=2)]
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn("run 64 was cancelled", out)
        self.assertIn("--dispatch starts a new one", out)
        self.assertNotIn("gh run rerun", out)
        self.push()
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(), [("workflow", "run", "macos.yml", "--ref", "main", "-f", "hosted=true")])

    def test_a_dispatch_needs_a_branch_whose_origin_tip_is_the_commit(self):
        self.push()
        pushed = self.sha
        (self.root / "macos/Fixture/Later.swift").write_text("// a later commit, not pushed\n", "utf8")
        self.commit("Later")
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertIn(f"isn't origin/main's tip (it has {pushed[:12]})", out)
        self.assertEqual(self.dispatched(), [], "a dispatch would test origin's older tip")
        self.push()
        git(self.root, "checkout", "-q", "--detach", "HEAD")  # origin's tip now, but on no branch
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(), [("workflow", "run", "macos.yml", "--ref", "main", "-f", "hosted=true")],
                         "the branch whose tip it is, though HEAD is on none")
        self.calls.clear()
        (self.root / "macos/Fixture/Detached.swift").write_text("// a commit on no branch\n", "utf8")
        self.commit("Detached")
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertIn(f"git push origin {self.sha}:refs/heads/release-{self.sha[:12]}", out)
        self.assertEqual(self.dispatched(), [])

    def test_a_dispatch_runs_the_origin_branch_whose_tip_is_the_release_commit(self):
        release = self.sha
        git(self.root, "push", "-q", "origin", f"{release}:refs/heads/release-1.0")
        (self.root / "notes.txt").write_text("another session's commit on top\n", "utf8")
        self.commit("Unrelated")
        self.push()
        code, out = self.gate("mac", "--commit", release, "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(),
                         [("workflow", "run", "macos.yml", "--ref", "release-1.0", "-f", "hosted=true")],
                         "main's tip is another commit: the branch that has the release commit on top")
        self.assertIn("started  macos.yml on release-1.0", out)

    def test_a_release_commit_under_others_is_told_how_to_push_it_alone(self):
        release = self.sha
        (self.root / "notes.txt").write_text("another session's commit on top\n", "utf8")
        self.commit("Unrelated")
        self.push()
        code, out = self.gate("mac", "--commit", release, "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertIn(f"{release[:12]} is no origin branch's tip", out)
        self.assertIn(f"`git push origin {release}:refs/heads/release-{release[:12]}`", out,
                      "pushing main again would still run main's tip, not the release commit")
        self.assertEqual(self.dispatched(), [])

    def test_a_release_commit_others_pushed_on_top_of_is_told_how_to_push_it_alone(self):
        # HEAD is the release commit on main, but another clone has since pushed a commit on top of it to origin's
        # main: pushing main is refused, and pulling would move HEAD past the release commit.
        self.push()
        other = self.root.parent / "other"
        git(self.root.parent, "clone", "-q", str(self.root.parent / "origin.git"), str(other))
        (other / "notes.txt").write_text("another session's commit on top\n", "utf8")
        git(other, "add", "notes.txt")
        git(other, "commit", "-q", "-m", "Unrelated")
        git(other, "push", "-q", "origin", "main")
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertNotIn("push the release commit first", out)
        self.assertIn(f"isn't origin/main's tip (it has {git(other, 'rev-parse', 'HEAD')[:12]}", out)
        self.assertIn(f"`git push origin {self.sha}:refs/heads/release-{self.sha[:12]}`", out)
        self.assertEqual(self.dispatched(), [])

    def test_a_run_in_progress_waits_with_the_watch_to_start_in_the_background(self):
        self.runs = [self.run_(14, status="in_progress")]
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertIn("gh run watch 14 --exit-status", out)
        self.assertIn("in the background", out)
        self.assertEqual(self.dispatched(), [], "one run per commit")

    def test_no_run_with_dispatch_needs_the_commit_pushed_then_starts_one(self):
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertIn("isn't origin/main's tip (it has nothing)", out)
        self.assertEqual(self.dispatched(), [], "a dispatch would test whatever origin's branch holds")
        self.push()
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(), [("workflow", "run", "macos.yml", "--ref", "main", "-f", "hosted=true")])
        self.assertIn("gh run watch 77 --exit-status", out)

    def test_a_dispatch_that_prints_no_run_still_names_the_uncommitted_changes(self):
        self.push()
        self.dispatch_out = ""  # a gh that doesn't print the new run's URL
        (self.root / "macos/project.yml").write_text("name: changed\n", "utf8")
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertIn(f"gh run list --workflow macos.yml --commit {self.sha}", out)
        self.assertIn("uncommitted changes the archive would ship, untested by the run: macos/project.yml", out)

    def test_a_named_commit_is_gated_instead_of_head(self):
        release = self.sha
        (self.root / "notes.txt").write_text("another session's commit on top\n", "utf8")
        git(self.root, "add", "notes.txt")
        git(self.root, "commit", "-q", "-m", "Unrelated")
        self.runs = [self.run_(15, sha=release)]
        self.assertEqual(self.gate("mac")[0], 1, "HEAD moved past the release commit")
        code, out = self.gate("mac", "--commit", release)
        self.assertEqual(code, 0, out)

    # --- the hosted Release launch ------------------------------------------------------------------------------------

    def test_a_caller_with_release_needs_the_hosted_launch_on_the_mac(self):
        self.release_builds()
        self.runs = [self.run_(20)]
        self.steps[20] = {"App tests": "success", release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn(release_gate.SMOKE_STEP, out)
        self.assertIn("-f hosted=true", out)
        self.push()
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(), [("workflow", "run", "macos.yml", "--ref", "main", "-f", "hosted=true")])
        self.steps[20][release_gate.SMOKE_STEP] = "success"
        self.calls.clear()
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 0, out)
        self.assertIn("hosted Release launch", out)
        self.assertEqual(self.dispatched(), [])

    def test_release_is_on_unless_the_caller_turns_it_off(self):
        # macos-app.yml builds the Release apps by default: the fixture's caller has no `release:` line.
        self.assertNotIn("release:", (self.root / ".github/workflows/macos.yml").read_text("utf8"))
        self.runs = [self.run_(22)]
        self.steps[22] = {"App tests": "success", release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 1, f"a caller that leaves release on needs the hosted launch: {out}")
        self.assertIn(f"without `{release_gate.SMOKE_STEP}`", out)
        self.release_builds(NO_RELEASE_WORKFLOW)
        self.runs = [self.run_(23)]
        self.steps[23] = {release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 0, f"`release: false` builds no Release app to launch: {out}")
        self.assertNotIn(("run", "view", "23", "--json", "jobs"), self.calls)
        self.release_builds(RELEASE_WORKFLOW.replace("release: true", "release: false  # tests only"))
        self.runs = [self.run_(24)]
        self.steps[24] = {release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 0, f"a comment after `release: false` leaves it off: {out}")

    def test_one_job_turning_release_off_leaves_the_release_jobs_launch_needed(self):
        jobs = RELEASE_WORKFLOW.split("jobs:\n")
        # The fixture's own caller has no `release:` line: a job that reached into the helper below it would take the
        # helper's `release: false` for its own.
        default = (self.root / ".github/workflows/macos.yml").read_text("utf8").rstrip("\n") + "\n"
        for helper, workflow in (("after", RELEASE_WORKFLOW + HELPER_JOB),
                                 ("before", f"{jobs[0]}jobs:\n{HELPER_JOB}{jobs[1]}"),
                                 ("after a job with no release line", default + HELPER_JOB)):
            with self.subTest(helper=helper):
                self.release_builds(workflow)
                self.runs = [self.run_(26)]
                self.steps[26] = {"App tests": "success", release_gate.SMOKE_STEP: "skipped"}
                code, out = self.gate("mac")
                self.assertEqual(code, 1, f"the release job builds Release and forwards hosted: {out}")
                self.assertIn(f"without `{release_gate.SMOKE_STEP}`", out)

    def test_only_the_jobs_with_inputs_count_not_its_key(self):
        for key, workflow, needed in (("release", NO_RELEASE_WORKFLOW, False), ("hosted", RELEASE_WORKFLOW, True),
                                      ("release", RELEASE_WORKFLOW.replace("with:", "with:  # the inputs"), True)):
            with self.subTest(key=key, needed=needed):
                self.release_builds(workflow.replace("  test:\n", f"  {key}:\n"))
                self.runs = [self.run_(28)]
                self.steps[28] = {"App tests": "success", release_gate.SMOKE_STEP: "skipped"}
                code, out = self.gate("mac")
                self.assertEqual(code, 1 if needed else 0, out)

    def test_a_with_on_one_line_is_refused_not_passed(self):
        self.release_builds(RELEASE_WORKFLOW.replace("""    with:
      hosted: ${{ inputs.hosted }}
      release: true""", """    with: { hosted: "${{ inputs.hosted }}", release: true }"""))
        self.runs = [self.run_(29)]
        self.steps[29] = {"App tests": "success", release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 2, f"its inputs can't be read line by line, so the gate can't say: {out}")
        self.assertIn("macos.yml: a job calling macos-app.yml has `with: {", out)
        self.assertIn("one input per line", out)

    def test_a_comment_at_column_0_ends_neither_the_job_nor_its_inputs(self):
        # JetBrains comments a line out at column 0. Read as an indent, the comment above `hosted:` would end the
        # `with:` block, hosted would read as unset and the gate would pass without the launch: the gate reads the
        # commit's workflow without its comment lines (`committed`), so no comment ends a block.
        old_call = "#    uses: vmihalachi/laching/.github/workflows/macos-app.yml@v0.4.0\n"
        self.release_builds(RELEASE_WORKFLOW.replace("    uses:", old_call + "    uses:")
                            .replace("    with:\n", "# the inputs\n    with:\n")
                            .replace("      hosted: ${{", "#      release: false\n      hosted: ${{"))
        self.push()
        self.runs = [self.run_(30)]
        self.steps[30] = {"App tests": "success", release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 1, f"the job still forwards hosted and builds Release: {out}")
        self.assertIn("(-f hosted=true)", out)
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(), [("workflow", "run", "macos.yml", "--ref", "main", "-f", "hosted=true")],
                         "the commented-out call is no second job")

    def test_a_forwarded_release_input_is_set_by_the_dispatch(self):
        self.release_builds(FORWARDED_RELEASE_WORKFLOW)
        self.push()
        self.runs = [self.run_(27)]
        self.steps[27] = {release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 1, f"a push run had the caller's release off: {out}")
        self.assertIn("(-f hosted=true -f release=true)", out)
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 3, out)
        self.assertEqual(self.dispatched(), [("workflow", "run", "macos.yml", "--ref", "main", "-f", "hosted=true",
                                              "-f", "release=true")],
                         "the caller's own default is off: without it the hosted run would skip the launch again")

    def test_the_iphone_release_needs_a_green_run_but_no_mac_launch(self):
        self.release_builds()
        self.runs = [self.run_(21)]
        self.steps[21] = {release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("ios")
        self.assertEqual(code, 2, "no mac.iosScheme: no iPhone and iPad build to gate")
        self.assertIn("mac.iosScheme", out)
        self.assertEqual(self.calls, [], "nothing asked of GitHub")
        self.with_ios()
        self.runs = [self.run_(21)]
        code, out = self.gate("ios")
        self.assertEqual(code, 0, out)
        self.assertNotIn(("run", "view", "21", "--json", "jobs"), self.calls)

    # --- the skips the hook recorded ----------------------------------------------------------------------------------

    def test_a_skip_over_a_shipped_path_refuses_even_on_a_green_run(self):
        self.runs = [self.run_(30)]
        self.skips = [{"check": "swift test (FixtureCore)", "reason": "needs a Swift toolchain",
                       "paths": ["macos/FixtureCore/Sources/Plan.swift"], "at": "2026-11-14T09:00:00Z"}]
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn("swift test (FixtureCore)", out)
        self.assertIn("needs a Swift toolchain", out)
        self.assertIn("macos/FixtureCore/Sources/Plan.swift", out)

    def test_a_skip_with_no_paths_counts_as_shipped(self):
        self.runs = [self.run_(31)]
        self.skips = [{"check": "the Laching pins agree", "reason": "timed out", "paths": [], "at": "2026-11-14"}]
        self.with_ios()
        self.runs = [self.run_(31)]
        self.assertEqual(self.gate("ios")[0], 1)

    def test_a_skip_over_what_ships_refuses_whatever_platform_its_check_tests(self):
        self.runs = [self.run_(35)]
        self.skips = [{"check": "gradle-core-test", "reason": "no JDK", "paths": ["conformance/scheduler/day.json"],
                       "at": "2026-11-14T09:00:00Z"}]
        code, out = self.gate("mac")
        self.assertEqual(code, 1, "the vectors ship in the Mac app too, whichever port's replay was skipped")
        self.assertIn("gradle-core-test (no JDK, 2026-11-14T09:00:00Z): conformance/scheduler/day.json", out)
        self.assertIn("whatever platform it tests", out, "says why an Android check holds a Mac release")
        self.assertIn("run it until it passes", out)

    def test_the_hook_is_asked_about_this_app_whatever_the_session_is_in(self):
        self.runs = [self.run_(36)]
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root.parent / "another-app")}):
            self.assertEqual(self.gate("mac")[0], 0)
        seen = json.loads((self.hook.parent / "env.json").read_text("utf8"))
        self.assertEqual(seen, {"CLAUDE_PROJECT_DIR": str(self.root), "COMPANION_ROOT": str(self.root)},
                         "hook.py reads $CLAUDE_PROJECT_DIR before $COMPANION_ROOT")

    def test_a_skip_only_over_another_platform_lets_the_release_go_and_is_listed(self):
        self.runs = [self.run_(32)]
        self.skips = [{"check": "WinUI app builds in the VM", "reason": "--no-vm",
                       "paths": ["windows/Fixture.App/MainWindow.cs"], "at": "2026-11-14T09:00:00Z"}]
        code, out = self.gate("mac")
        self.assertEqual(code, 0, out)
        self.assertIn("not blocking: WinUI app builds in the VM (--no-vm, 2026-11-14T09:00:00Z): "
                      "windows/Fixture.App/MainWindow.cs", out, "deferred elsewhere in the same tree, so named")

    def test_a_skip_path_is_compared_from_the_app_however_it_is_written(self):
        self.runs = [self.run_(34)]
        for path in (str(self.root / "windows/Fixture.App/MainWindow.cs"), "./windows/Fixture.App/MainWindow.cs",
                     "windows\\Fixture.App\\MainWindow.cs", "macos/../windows/Fixture.App/MainWindow.cs"):
            self.skips = [{"check": "WinUI app builds in the VM", "reason": "--no-vm", "paths": [path], "at": "now"}]
            code, out = self.gate("mac")
            self.assertEqual(code, 0, f"{path}: {out}")
        for path in ("./macos/Fixture/App.swift", str(self.root / "macos/Fixture/App.swift"), "/elsewhere/windows/x",
                     "windows/../macos/Fixture/App.swift"):
            self.skips = [{"check": "xcodebuild test", "reason": "busy", "paths": [path], "at": "now"}]
            self.assertEqual(self.gate("mac")[0], 1, path)

    def test_skips_refuse_before_any_dispatch(self):
        self.push()
        self.skips = [{"check": "xcodebuild test", "reason": "the simulator was busy",
                       "paths": ["macos/Fixture/App.swift"], "at": "2026-11-14T09:00:00Z"}]
        code, out = self.gate("mac", "--dispatch")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.dispatched(), [], "run the skipped check first; CI on the same commit can wait")

    def test_a_hook_that_lists_no_skips_fails_closed(self):
        self.runs = [self.run_(33)]
        self.skips = None
        code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        self.assertIn("skips --json", out)

    def test_the_real_hooks_skip_is_named_with_its_check_and_time(self):
        # The hook itself, reading verify.sh's log in the app's Git folder: its `at` is seconds since 1970.
        self.runs = [self.run_(37)]
        at = 1794646800  # 2026-11-14
        log = Path(git(self.root, "rev-parse", "--absolute-git-dir")) / "laching-slow-checks.log"
        log.write_text(f"skip\t{at}\txcodebuild test\tthe simulator was busy\tmacos/Fixture/App.swift\n", "utf8")
        with mock.patch.object(release_gate, "HOOK", TOOLS / "hooks" / "hook.py"):
            code, out = self.gate("mac")
        self.assertEqual(code, 1, out)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
        self.assertIn(f"xcodebuild test (the simulator was busy, {when}): macos/Fixture/App.swift", out)
        self.assertNotIn(str(at), out, "a date, not seconds since 1970")
        self.assertIn(f"(busy, {10**20})", release_gate.describe({"check": "x", "reason": "busy", "at": 10**20}),
                      "a time no calendar has is shown as the log wrote it, not a crash")

    # --- what it reads ------------------------------------------------------------------------------------------------

    def test_uncommitted_shipped_changes_are_named(self):
        self.runs = [self.run_(40)]
        (self.root / "macos/project.yml").write_text("name: changed\n", "utf8")
        (self.root / "windows/Fixture.Core/Fixture.Core.csproj").write_text("<Project/>\n", "utf8")
        (self.root / "macos/Fixture/New Feature.swift").write_text("// not added yet\n", "utf8")
        (self.root / "windows/Fixture.Core/New.cs").write_text("// not added yet\n", "utf8")
        code, out = self.gate("mac")
        self.assertEqual(code, 0, "a warning: the tag names the commit, the archive is the step that reads the tree")
        self.assertIn("macos/project.yml", out)
        self.assertIn("macos/Fixture/New Feature.swift", out, "an untracked file the archive would build in")
        self.assertNotIn("Fixture.Core.csproj", out)
        self.assertNotIn("New.cs", out)

    def test_the_workflow_is_the_caller_of_macos_app_or_the_one_named(self):
        workflows = self.root / ".github/workflows"
        (workflows / "macos.yml").write_text(
            "# calls vmihalachi/laching/.github/workflows/macos-app.yml@v0.5.0 no more\njobs: {}\n", "utf8")
        self.commit("No caller")
        self.assertEqual(self.gate("mac")[0], 2, "a comment isn't a call")
        (workflows / "apple.yml").write_text(RELEASE_WORKFLOW, "utf8")
        self.assertEqual(self.gate("mac")[0], 2, "a caller the commit doesn't have, its run didn't use")
        (workflows / "nightly.yml").write_text(RELEASE_WORKFLOW, "utf8")
        self.commit("Two callers")
        code, out = self.gate("mac")
        self.assertEqual(code, 2, out)
        self.assertIn("--workflow", out)
        self.runs = [self.run_(50)]
        self.steps[50] = {release_gate.SMOKE_STEP: "success"}
        code, out = self.gate("mac", "--workflow", "apple.yml")
        self.assertEqual(code, 0, out)
        self.assertIn(("run", "list", "--workflow", "apple.yml"), [call[:4] for call in self.calls])

    def test_the_launch_it_needs_is_the_commits_not_the_working_trees(self):
        self.release_builds(NO_RELEASE_WORKFLOW)
        self.runs = [self.run_(51)]
        self.steps[51] = {release_gate.SMOKE_STEP: "skipped"}
        (self.root / ".github/workflows/macos.yml").write_text(RELEASE_WORKFLOW, "utf8")
        code, out = self.gate("mac")
        self.assertEqual(code, 0, f"the commit's caller builds no Release app: {out}")
        self.assertNotIn(("run", "view", "51", "--json", "jobs"), self.calls)
        self.assertIn(".github/workflows/macos.yml", out, "named as uncommitted, all the same")
        self.release_builds()
        (self.root / ".github/workflows/macos.yml").write_text(NO_RELEASE_WORKFLOW, "utf8")
        self.runs = [self.run_(52)]
        self.steps[52] = {release_gate.SMOKE_STEP: "skipped"}
        code, out = self.gate("mac")
        self.assertEqual(code, 1, f"the commit's caller builds Release, whatever the tree says now: {out}")
        self.assertIn(release_gate.SMOKE_STEP, out)

    def test_the_workflows_are_the_repositorys_top_ones_for_an_app_in_a_subfolder(self):
        # GitHub runs only the workflows at the repository's top; the app's companion.json is in a folder of it.
        mono = self.root.parent / "mono"
        shutil.copytree(self.root, mono / "app", ignore=shutil.ignore_patterns(".git"))
        (mono / "app/.github").rename(mono / ".github")
        (mono / ".github/workflows/macos.yml").write_text(RELEASE_WORKFLOW, "utf8")
        (mono / "app/.github/workflows").mkdir(parents=True)
        (mono / "app/.github/workflows/nightly.yml").write_text(RELEASE_WORKFLOW, "utf8")  # GitHub never runs it
        git(mono, "init", "-q")
        git(mono, "add", "-A")
        git(mono, "commit", "-q", "-m", "Release 1.0 (4)")
        self.root, self.sha = mono / "app", git(mono, "rev-parse", "HEAD")
        self.runs = [self.run_(70)]
        self.steps[70] = {release_gate.SMOKE_STEP: "success"}
        with mock.patch.dict(os.environ, {"COMPANION_ROOT": str(self.root)}):
            code, out = self.gate("mac")
            self.assertEqual(code, 0, out)
            self.assertIn("macos.yml run 70 passed", out)
            self.assertIn(("run", "view", "70", "--json", "jobs"), self.calls, "the top's caller builds Release")
            self.assertEqual(self.gate("mac", "--workflow", "nightly.yml")[0], 2, "not one of the repository's")

    def test_a_companion_json_that_cant_be_read_is_a_setup_error(self):
        (self.root / "companion.json").write_text("{ not json", "utf8")
        code, out = self.gate("mac")
        self.assertEqual(code, 2, out)
        self.assertIn("companion", out)
        manifest = {"laching": {"tag": "v0.5.0"}, "mac": {"dir": 3}}
        (self.root / "companion.json").write_text(json.dumps(manifest), "utf8")
        self.assertEqual(self.gate("mac")[0], 2, "invalid")
        self.assertEqual(self.calls, [])

    def test_usage(self):
        self.assertEqual(self.gate()[0], 2)
        self.assertEqual(self.gate("windows")[0], 2, "Windows ships what windows-release.yml builds and tests")


if __name__ == "__main__":
    unittest.main()
