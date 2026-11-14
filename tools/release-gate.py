#!/usr/bin/env python3
"""Refuses a Mac or iPhone and iPad release that no full run has seen: the app's workflow that calls Laching's
macos-app.yml must have passed on the exact commit the tag will name, and no check the agent hook recorded as skipped
(`hooks/hook.py skips --json`) may cover a path that ships. Run it from the app once the release commit is pushed, and
again before submitting and tagging.

    python3 laching/tools/release-gate.py mac                  # HEAD's run passed and nothing that ships was skipped
    python3 laching/tools/release-gate.py ios
    python3 laching/tools/release-gate.py mac --dispatch       # no run on HEAD yet: start one (HEAD pushed first)
    python3 laching/tools/release-gate.py mac --commit <sha>   # the release commit, when other commits sit on top
    python3 laching/tools/release-gate.py mac --workflow apple.yml   # when more than one workflow calls macos-app.yml

Exit status: 0 the release may go ahead; 1 refused, with why and what clears it; 2 a usage or setup error; 3 a run on
the commit is queued or in progress (or was just started): start the `gh run watch <id> --exit-status` it prints in
the background and run the gate again when that ends.

Only a run whose `headSha` is the commit counts, from any event (a push, a dispatch, a pull request), and the newest one
ended decides: a failed run refuses (fix it in a new commit, or rerun a flake: `gh run rerun <id> --failed`), a
cancelled one is started again (`--dispatch`); a green run on the same commit wins over either. The caller and what it
needs are read from the commit's own workflow files, the ones its run used (at the repository's top, where GitHub reads
them, whatever folder holds the app). On the Mac, a caller whose macos-app.yml job builds the Release apps (its
`release` is on unless that job sets `release: false`) and forwards a `hosted` input also has to have launched the
Release app on GitHub's runner (the `Launch the Release app` step, which a self-hosted run skips): `--dispatch` starts
that run with `-f hosted=true` (and `-f release=true` when the job forwards an input as `release` too), which the iOS
gate then accepts too. A dispatch runs a branch's tip, so it needs the commit to be the tip of a branch on origin: the
current one, or any other (a release branch pushed with `git push origin <sha>:refs/heads/<name>` when other commits sit
on top of it). A skip touches what ships unless every path it names (relative to the app, or absolute inside it) lies
under `windows.dir`, `android.dir` or `landing.dir`, whatever platform its check tests (an Android replay skipped over
conformance/ holds a Mac release); one naming no path counts as touching, and a hook that prints no list (a Laching
without `hook.py skips`) refuses too. The other skips are listed, not blocking. Changes not yet committed under a
shipped path, untracked files too, are named as a warning: the archive builds the working tree. Needs the GitHub CLI
(`gh`), signed in; it never pushes, tags or reruns anything.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path

import companion
import conform

TOOLS = Path(__file__).resolve().parent
HOOK = TOOLS / "hooks" / "hook.py"
"""The agent hook whose `skips --json` lists the open skips."""

SMOKE_STEP = "Launch the Release app"
"""macos-app.yml's step that launches the Release app, on GitHub's runner only."""

CALLS = "macos-app"
"""The reusable workflow the app's release workflow calls."""

WAITING = 3
"""The exit status while a run on the commit is still to end."""

STOPPED = ("cancelled", "skipped", "stale")
"""The conclusions of a run that ended without testing the commit through: started again, not fixed or rerun."""


def gh(root: Path, *args: str) -> str:
    """The GitHub CLI in the app's folder, so it asks the app's repository: its stdout, with stderr too unless the
    command asks for JSON (`gh workflow run` prints the run's URL on either). A failure stops the gate with exit 2."""
    try:
        result = subprocess.run(["gh", *args], cwd=root, capture_output=True, encoding="utf8")
    except FileNotFoundError:
        companion.fail("needs the GitHub CLI (gh), signed in")
    if result.returncode:
        companion.fail(f"gh {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout if "--json" in args else result.stdout + result.stderr


def git(root: Path, *args: str) -> str:
    """git in the app, its stdout stripped; a failure stops the gate with exit 2."""
    result = companion.git(root, *args)
    if result.returncode:
        companion.fail(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def load() -> companion.Config:
    """The app's companion.json; one that can't be read or is invalid stops the gate with exit 2, a setup error."""
    try:
        return companion.load()
    except SystemExit as stop:
        if isinstance(stop.code, str):
            companion.fail(stop.code)
        raise


def committed(root: Path, sha: str, name: str) -> str | None:
    """The workflow `name` (under the repository's .github/workflows) as the commit has it, without its comment lines,
    as `conform.workflow_code` reads one; None when the commit has no such file."""
    # `<sha>:<path>` is from Git's root, where GitHub reads the workflows, whatever folder of it holds the app.
    result = companion.git(root, "show", f"{sha}:.github/workflows/{name}")
    if result.returncode:
        return None
    return "\n".join(line for line in result.stdout.splitlines() if not line.lstrip().startswith("#"))


def caller(root: Path, sha: str, named: str | None) -> str:
    """The workflow file (its name under the repository's .github/workflows) that calls macos-app.yml in the commit, or
    the one named: the run on the commit used the commit's files, whatever the working tree has since."""
    if named:
        if committed(root, sha, named) is None:
            companion.fail(f"{sha[:12]} has no .github/workflows/{named}")
        return named
    listed = git(root, "ls-tree", "--full-tree", "--name-only", sha, ".github/workflows/").splitlines()
    found = []
    for name in sorted(posixpath.basename(path) for path in listed if re.search(r"\.ya?ml$", path)):
        code = committed(root, sha, name) or ""
        if any(match.group(1) == CALLS for match in conform.REUSABLE.finditer(code)):
            found.append(name)
    if not found:
        companion.fail(f"no workflow in {sha[:12]}'s .github/workflows calls Laching's {CALLS}.yml")
    if len(found) > 1:
        companion.fail(f"{', '.join(found)} all call {CALLS}.yml: name the release one with --workflow")
    return found[0]


def calling_jobs(code: str) -> list[str]:
    """Each job of a workflow's code that calls macos-app.yml: its lines, from its key to the next job's. `code` is
    the workflow without its comment lines (`committed`): a comment at column 0 would otherwise end a job early."""
    lines = code.splitlines()
    depth = [len(line) - len(line.lstrip()) if line.strip() else None for line in lines]
    jobs = []
    for at, line in enumerate(lines):
        if not any(match.group(1) == CALLS for match in conform.REUSABLE.finditer(line)):
            continue
        # The job's key is the nearest line above that sits further out than `uses:`; the job ends where a line sits
        # no further in than its key.
        start = next((i for i in range(at - 1, -1, -1) if depth[i] is not None and depth[i] < depth[at]), at)
        end = next((i for i in range(at + 1, len(lines)) if depth[i] is not None and depth[i] <= depth[start]),
                   len(lines))
        jobs.append("\n".join(lines[start:end]))
    return jobs


def inputs_of(job: str, workflow: str) -> str:
    """The lines of a calling job's `with:` mapping, the inputs it passes; empty when it has none. A `with:` on one line
    (a flow mapping, `with: { hosted: true }`) is refused: read line by line its inputs would be missed, and the gate
    would pass without the launch."""
    lines = job.splitlines()
    depth = [len(line) - len(line.lstrip()) if line.strip() else None for line in lines]
    inner = min((d for d in depth[1:] if d is not None), default=None)  # where the job's own keys sit: `uses:`, `with:`
    for at in range(1, len(lines)):
        found = re.match(r"\s*with:(.*)$", lines[at])
        if not found or depth[at] != inner:
            continue
        if re.sub(r"(^|\s+)#.*$", "", found.group(1)).strip():
            companion.fail(f"{workflow}: a job calling {CALLS}.yml has `{lines[at].strip()}` on one line: put one "
                           "input per line under `with:`, so the gate can read them")
        end = next((i for i in range(at + 1, len(lines)) if depth[i] is not None and depth[i] <= inner), len(lines))
        return "\n".join(lines[at + 1:end])
    return ""


def setting(inputs: str, name: str) -> str | None:
    """The value a job's inputs (`inputs_of`) give `name`, without a comment after it or quotes; None when they give
    none. Only `with:` is read: a job keyed `release:` says nothing about the input."""
    found = re.search(rf"^\s*{re.escape(name)}:\s*(.*?)\s*$", inputs, re.M)
    return re.sub(r"(^|\s+)#.*$", "", found.group(1)).strip("'\"").strip() if found else None


def launch_fields(root: Path, sha: str, workflow: str) -> list[str] | None:
    """What a dispatch sets (`-f` fields) so its run launches the Mac Release app on GitHub's runner; None when the
    commit's caller has no job that would. Such a job calls macos-app.yml with `release` on (macos-app.yml's default:
    only `release: false` turns it off) and `hosted` either `true` or one of the caller's inputs. Each input the job
    forwards as `hosted` or `release` is set true, since the caller's own default may be off."""
    fields: list[str] | None = None
    for job in calling_jobs(committed(root, sha, workflow) or ""):
        inputs = inputs_of(job, workflow)
        hosted, release = setting(inputs, "hosted"), setting(inputs, "release")
        if (release or "").lower() == "false" or hosted is None:
            continue
        forwarded = [f"{name}=true" for value in (hosted, release or "")
                     for name in re.findall(r"\binputs\.([\w-]+)", value)]
        if hosted.lower() != "true" and not re.search(r"\binputs\.", hosted):
            continue  # never on GitHub's runner, or not for a dispatch to choose
        fields = fields or []
        fields += [field for field in forwarded if field not in fields]
    return fields


def launched(root: Path, run: int) -> bool:
    """True when the run's Release launch step passed."""
    jobs = json.loads(gh(root, "run", "view", str(run), "--json", "jobs")).get("jobs") or []
    return any(step.get("name") == SMOKE_STEP and step.get("conclusion") == "success"
               for job in jobs for step in job.get("steps") or [])


def elsewhere(config: companion.Config) -> list[str]:
    """The folders of the app's other platforms, relative to the root: nothing under them ships to the App Store."""
    folders = []
    for key in ("windows.dir", "android.dir", "landing.dir"):
        path = config.path(key)
        if path and path != config.root:
            folders.append(companion.rel(path, config.root))
    return folders


def ships(path: str, others: list[str]) -> bool:
    """True when `path` (relative to the root, forward slashes) lies outside every one of `others`."""
    return not any(path == folder or path.startswith(f"{folder}/") for folder in others)


def from_root(path: object, root: Path) -> str:
    """A skip's path as `ships` compares it: relative to the app with forward slashes, `./` and `a/..` folded away.
    An absolute one inside the app is made relative; one outside it stays absolute, so it ships."""
    text = str(path).replace("\\", "/")
    if Path(text).is_absolute() or re.match(r"[A-Za-z]:/", text):
        text = companion.rel(text, root)
    return posixpath.normpath(text) if text else text


def open_skips(root: Path) -> list[dict] | None:
    """The hook's open skips, each {"check", "reason", "paths", "at"}; None when it prints no such list."""
    # The hook finds the app from $CLAUDE_PROJECT_DIR first, which a session may have set to another checkout.
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "COMPANION_ROOT": str(root)}
    result = subprocess.run([sys.executable, str(HOOK), "skips", "--json"], cwd=root, stdin=subprocess.DEVNULL,
                            capture_output=True, encoding="utf8", env=env)
    try:
        skips = json.loads(result.stdout)
    except ValueError:
        return None
    if result.returncode or not isinstance(skips, list) or not all(isinstance(skip, dict) for skip in skips):
        return None
    return skips


def describe(skip: dict) -> str:
    """One skip on one line: the check, why and when, and up to three of its paths."""
    paths = [str(path) for path in skip.get("paths") or []]
    named = ", ".join(paths[:3]) + (f" and {len(paths) - 3} more" if len(paths) > 3 else "")
    when = at = skip.get("at", "?")  # the hook's is seconds since 1970: shown as a local date and time
    if isinstance(at, (int, float)):
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
        except (OverflowError, OSError, ValueError):  # a time no calendar has: as the log wrote it
            when = str(at)
    why = f"{skip.get('reason', 'no reason given')}, {when}"
    return f"{skip.get('check', '?')} ({why}): {named or 'no path'}"


def uncommitted(root: Path) -> list[str]:
    """The app's paths that differ from HEAD: changed, staged, deleted, and untracked but not ignored."""
    result = companion.git(root, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all", ".")
    if result.returncode:
        companion.fail(f"git status: {result.stderr.strip()}")
    # Each entry is "XY path", the path from Git's root: made the app's, as the skips' are.
    top = Path(git(root, "rev-parse", "--show-toplevel"))
    return [from_root(top / entry[3:], root) for entry in result.stdout.split("\0") if len(entry) > 3]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release-gate.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("platform", choices=("mac", "ios"), help="the release: the Mac app, or the iPhone and iPad one")
    parser.add_argument("--commit", default="HEAD", help="the commit the tag will name (default HEAD)")
    parser.add_argument("--workflow", help="the app's workflow that calls macos-app.yml, when more than one does")
    parser.add_argument("--dispatch", action="store_true", help="start a run when the commit has none that counts")
    args = parser.parse_args(argv)

    config = load()
    root = config.root
    if not config.get("mac.scheme" if args.platform == "mac" else "mac.iosScheme"):
        companion.fail(f"companion.json names no {'mac.scheme' if args.platform == 'mac' else 'mac.iosScheme'}: "
                       f"the app ships no {'Mac' if args.platform == 'mac' else 'iPhone and iPad'} build to gate")
    sha = git(root, "rev-parse", "--verify", f"{args.commit}^{{commit}}")
    short = sha[:12]
    workflow = caller(root, sha, args.workflow)
    fields = launch_fields(root, sha, workflow) if args.platform == "mac" else None
    launch = fields is not None
    hosted = " ".join(f"-f {field}" for field in fields or [])
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    print(f"release-gate {args.platform}: {short} ({branch}), {workflow}")
    refused: list[str] = []
    watch: str | None = None
    others = elsewhere(config)

    skips = open_skips(root)
    if skips is None:
        refused.append(f"can't list the open skips: `{companion.rel(HOOK, root)} skips --json` printed no list "
                       "(it needs a Laching whose hook has the command)")
    else:
        for skip in skips:
            paths = [from_root(path, root) for path in skip.get("paths") or []]
            if not paths or any(ships(path, others) for path in paths):
                refused.append(f"skipped over what ships: {describe(skip)}; a skip over a path that ships holds the "
                               "release whatever platform it tests: run it until it passes (scripts/verify.sh "
                               "routes it), which clears the skip")
            else:
                print(f"  note     open skip outside what ships, not blocking: {describe(skip)}")

    runs = json.loads(gh(root, "run", "list", "--workflow", workflow, "--commit", sha, "--json",
                         "databaseId,status,conclusion,headSha,event,url,createdAt", "--limit", "20"))
    runs = sorted((run for run in runs if run.get("headSha") == sha), key=lambda run: run.get("createdAt") or "",
                  reverse=True)  # newest first: the newest run that ended decides, but for a green one
    done = [run for run in runs if run.get("status") == "completed"]
    green = [run for run in done if run.get("conclusion") == "success"]
    passed = next((run for run in green if not launch or launched(root, run["databaseId"])), None)
    active = [run for run in runs if run.get("status") != "completed"]
    latest = done[0] if done else None
    start = False
    if passed:
        print(f"  ok       {workflow} run {passed['databaseId']} passed on {short}"
              + (", with the hosted Release launch" if launch else ""))
    elif active:
        watch = str(active[0]["databaseId"])
        print(f"  waiting  {workflow} run {watch} is {active[0].get('status')} on {short}")
    elif latest is None:
        start = args.dispatch
        if not start:
            refused.append(f"no {workflow} run on {short}: --dispatch starts one (push the commit first)")
    elif latest.get("conclusion") == "success":
        start = args.dispatch
        if not start:
            refused.append(f"{workflow} run {latest['databaseId']} passed on {short} without `{SMOKE_STEP}` (a "
                           "self-hosted run): --dispatch starts one on GitHub's runner"
                           + (f" ({hosted})" if hosted else ""))
    elif latest.get("conclusion") in STOPPED:
        start = args.dispatch
        if not start:
            refused.append(f"{workflow} run {latest['databaseId']} was {latest.get('conclusion')} on {short} before "
                           "it tested the commit through: --dispatch starts a new one")
    else:
        number = latest["databaseId"]
        refused.append(f"{workflow} run {number} {latest.get('conclusion') or 'failed'} on {short}: fix it in a new "
                       f"commit, or rerun a flake with `gh run rerun {number} --failed`")

    head = git(root, "rev-parse", "HEAD")
    if head != sha:
        print(f"  warning  HEAD is {head[:12]}, not {short}: the archive builds the working tree, so build it from a "
              f"checkout of {short}")
    else:
        dirty = [path for path in uncommitted(root) if ships(path, others)]
        if dirty:
            print("  warning  uncommitted changes the archive would ship, untested by the run: "
                  + ", ".join(dirty[:5]) + (f" and {len(dirty) - 5} more" if len(dirty) > 5 else ""))

    if start and refused:
        print(f"  held     no {workflow} run started while the gate refuses")
    elif start:
        # A dispatch runs a branch's tip: the current branch's when it is the commit, else any origin branch's that is.
        tips = {}
        for line in git(root, "ls-remote", "--heads", "origin").splitlines():
            tip, _, ref = line.partition("\t")
            tips[ref.removeprefix("refs/heads/")] = tip
        on = sorted(name for name, tip in tips.items() if tip == sha)
        ref = branch if branch in on else (on[0] if on else None)
        mine = head == sha and branch != "HEAD"  # the commit is the current branch's own tip, here
        # Pushing the branch reaches origin only when origin's tip is behind the commit (or there is none): once others
        # pushed on top, it's refused, and pulling them moves HEAD past the release commit.
        behind = mine and (branch not in tips or companion.git(root, "merge-base", "--is-ancestor", tips[branch],
                                                               sha).returncode == 0)
        if ref is None and behind:
            tip = tips[branch][:12] if branch in tips else "nothing"
            refused.append(f"{short} isn't origin/{branch}'s tip (it has {tip}), nor any other origin branch's, and a "
                           "dispatch runs a branch's tip: push the release commit first, then run this again")
        elif ref is None:
            where = (f"isn't origin/{branch}'s tip (it has {tips[branch][:12]}, which others pushed on top or beside "
                     "it), nor any other origin branch's" if mine else "is no origin branch's tip")
            refused.append(f"{short} {where}, and a dispatch runs a branch's tip: push it to a branch of its own "
                           f"(`git push origin {sha}:refs/heads/release-{short}`), then run this again")
        else:
            out = gh(root, "workflow", "run", workflow, "--ref", ref,
                     *(part for field in fields or [] for part in ("-f", field)))
            found = re.search(r"/actions/runs/(\d+)", out)
            watch = found.group(1) if found else None
            print(f"  started  {workflow} on {ref}" + (" (hosted)" if launch else "")
                  + ("" if watch else f": find it with `gh run list --workflow {workflow} --commit {sha}`"))
            if not watch:
                return WAITING

    for reason in refused:
        print(f"  refused  {reason}")
    if watch:
        print(f"Start `gh run watch {watch} --exit-status` in the background, then run this gate again when it ends.")
    if refused:
        print("Refused: submit and tag nothing until the gate passes.")
        return 1
    if watch:
        return WAITING
    print(f"The {args.platform} release may go ahead on {short}.")
    return 0


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
