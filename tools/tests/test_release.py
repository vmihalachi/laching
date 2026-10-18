#!/usr/bin/env python3
"""Tests for scripts/release.sh (`plan`, `nuget`, `web`, `landing`, `bump`) and release.yml's plan step itself, over a copy of
this repository's version files and .NET projects (the web packages' too) tagged v0.13.0 in a temporary git repository, with `gh` and `npm`
stood in for."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import NO_SHEBANG, REPO, WINDOWS

WORKFLOW = REPO / ".github/workflows/release.yml"
COPIED = ("scripts/release.sh", "scripts/verify.sh", "Sources/LachingCore/Laching.swift", "kotlin/gradle.properties",
          "README.md", "site/index.html", "dotnet/Directory.Build.props", "web/Directory.Build.props",
          "web/Directory.Build.targets", "web/Directory.Packages.props", "tools/landing/package.json")
# gh api repos/…/commits/<sha>/pulls --jq …: the lines the --jq makes, from $FAKE_PULLS/<sha> ("number<tab>labels");
# "Bad credentials" without that folder, and GitHub's empty answer as many times as $FAKE_PULLS/<sha>.empty says.
# `gh workflow run` is logged, and so is `sleep` (which waits for nothing).
GH = ('[ "$1" = workflow ] && { echo "$*" >> "$FAKE_PULLS.log"; exit "${FAKE_GH_WORKFLOW_STATUS:-0}"; }\n'
      '[ -d "$FAKE_PULLS" ] || { echo "HTTP 401: Bad credentials" >&2; exit 1; }\n'
      'sha=$(echo "$2" | cut -d/ -f5); empty="$FAKE_PULLS/$sha.empty"\n'
      'if [ "$(cat "$empty" 2>/dev/null || echo 0)" -gt 0 ]; then\n'
      '  echo $(($(cat "$empty") - 1)) > "$empty"; echo "unexpected end of JSON input" >&2; exit 1\n'
      'fi\n'
      '[ ! -f "$FAKE_PULLS/$sha" ] || cat "$FAKE_PULLS/$sha"\n')
SLEEP = 'echo "$*" >> "$FAKE_PULLS.sleep"\n'


@unittest.skipIf(WINDOWS, NO_SHEBANG)
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.root = self.tmp / "laching"
        # The version files, the projects and the tools' shell scripts (which verify.sh may source).
        for file in [*(REPO / name for name in COPIED), *(REPO / "dotnet").glob("*/*.csproj"), *(REPO / "web/src").glob("*/*.csproj"),
                     *(REPO / "tools").glob("*.sh")]:
            (self.root / file.relative_to(REPO)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, self.root / file.relative_to(REPO))
        # Whatever this repository's release, the copy is at 0.13.0, NuGet and the web packages with it.
        version = re.search(r'version = "([^"]+)"', self.read("Sources/LachingCore/Laching.swift")).group(1)
        for name in ("Sources/LachingCore/Laching.swift", "kotlin/gradle.properties", "README.md", "site/index.html"):
            self.write(name, self.read(name).replace(version, "0.13.0"))
        self.write("dotnet/Directory.Build.props", re.sub(r"(<LachingVersion[^>]*>)[^<]+", r"\g<1>0.13.0",
                                                          self.read("dotnet/Directory.Build.props")))
        self.write("web/Directory.Build.props", re.sub(r"(<LachingWebVersion[^>]*>)[^<]+", r"\g<1>0.13.0",
                                                       self.read("web/Directory.Build.props")))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("gh", GH), ("sleep", SLEEP),
                           ("npm", '[ "$1 $2 $3" = "view @laching/landing version" ] && echo 0.13.0\n')):
            (self.bin / name).write_text("#!/bin/sh\n" + body, "utf8")
            (self.bin / name).chmod(0o755)
        self.pulls: dict[str, list[dict]] = {}
        self.empty: dict[str, int] = {}  # how many times GitHub answers nothing for a commit
        self.git("init", "-q")
        self.git("remote", "add", "origin", "https://github.com/vmihalachi/laching.git")
        self.commit("0.13.0: release")
        self.git("tag", "-a", "v0.13.0", "-m", "Laching 0.13.0")

    def git(self, *args: str) -> str:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        command = ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "-c", "init.defaultBranch=master",
                   "-c", "tag.gpgSign=false", "-c", "commit.gpgSign=false", *args]
        return subprocess.run(command, cwd=self.root, env=env, check=True,
                              capture_output=True, encoding="utf8").stdout.strip()

    def read(self, name: str) -> str:
        return (self.root / name).read_text("utf8")

    def write(self, name: str, text: str) -> None:
        (self.root / name).parent.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(text, "utf8")

    def commit(self, message: str, pr: int | None = None, labels: tuple[str, ...] = ()) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        if pr is not None:
            self.pulls[self.git("rev-parse", "HEAD")] = [{"number": pr, "labels": list(labels)}]

    def change(self, path: str, pr: int | None = None, labels: tuple[str, ...] = ()) -> None:
        self.write(path, f"// {path} {self.git('rev-list', '--count', 'HEAD')}\n")
        self.commit(f"change {path}", pr, labels)

    def env(self, pulls: bool = True, **extra: str) -> dict[str, str]:
        """The environment with the fakes; `pulls=False` makes gh fail, as it does without a token."""
        pulls_file = self.tmp / "pulls"
        shutil.rmtree(pulls_file, ignore_errors=True)
        (self.tmp / "pulls.sleep").unlink(missing_ok=True)
        if pulls:
            pulls_file.mkdir()
            for sha, prs in self.pulls.items():
                (pulls_file / sha).write_text("".join(f"{pr['number']}\t{','.join(pr['labels'])}\n" for pr in prs), "utf8")
            for sha, times in self.empty.items():
                (pulls_file / f"{sha}.empty").write_text(f"{times}\n", "utf8")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_") and key not in ("GITHUB_REPOSITORY", "GH_TOKEN")}
        return {**env, "PATH": f"{self.bin}{os.pathsep}{env.get('PATH', '')}", "FAKE_PULLS": str(pulls_file), **extra}

    def run_release(self, *args: str, pulls: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(self.root / "scripts/release.sh"), *args], cwd=self.root, env=self.env(pulls),
                              capture_output=True, encoding="utf8")

    def out(self, *args: str) -> str:
        result = self.run_release(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def test_the_plan_numbers_the_release_from_the_breaking_label(self):
        self.assertEqual(self.out("plan"), "none (no commits since v0.13.0)")
        self.change("docs/a.md", pr=101)
        self.change("docs/b.md", pr=101)
        self.change("docs/c.md")  # pushed to master, no pull request
        self.assertEqual(self.out("plan"), "release 0.13.1 (patch: none of the 1 pull request merged is labelled "
                                           "breaking; 3 commits since v0.13.0)")
        self.change("docs/d.md", pr=102, labels=("enhancement", "breaking"))
        self.change("docs/e.md", pr=103, labels=("breaking",))
        self.assertEqual(self.out("plan"), "release 0.14.0 (minor: #102, #103 labelled breaking (a minor while 0.x); "
                                           "5 commits since v0.13.0)")
        self.assertEqual(self.out("plan", "--bump", "major"), "release 1.0.0 (major: asked for; 5 commits since v0.13.0)")
        self.assertEqual(self.run_release("plan", "--bump", "huge").returncode, 1)

        result = self.run_release("plan", pulls=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Bad credentials", result.stderr)
        self.assertEqual(self.run_release("plan", "--bump", "patch", pulls=False).returncode, 0, "reads no labels")
        self.out("bump", "1.0.0")
        self.commit("1.0.0: release")
        self.git("tag", "-a", "v1.0.0", "-m", "Laching 1.0.0")
        self.change("docs/f.md", pr=104, labels=("breaking",))
        self.assertTrue(self.out("plan").startswith("release 2.0.0 (major: #104 labelled breaking;"))
        self.write("Sources/LachingCore/Laching.swift", self.read("Sources/LachingCore/Laching.swift").replace(
            '"1.0.0"', '"1.0.5"'))
        self.commit("a bump by hand")
        result = self.run_release("plan", "--bump", "patch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("says 1.0.5, but the last release tag is v1.0.0", result.stderr)

    def test_the_plan_asks_github_again_when_it_answers_nothing(self):
        self.change("docs/a.md", pr=101, labels=("breaking",))
        sha = self.git("rev-parse", "HEAD")
        slept = self.tmp / "pulls.sleep"
        self.empty[sha] = 2
        self.assertEqual(self.out("plan"), "release 0.14.0 (minor: #101 labelled breaking (a minor while 0.x); "
                                           "1 commit since v0.13.0)")
        self.assertEqual(slept.read_text("utf8"), "2\n4\n")

        self.empty[sha] = 3
        result = self.run_release("plan")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"couldn't ask GitHub for {sha}'s pull request: unexpected end of JSON input", result.stderr)
        self.assertEqual(slept.read_text("utf8"), "2\n4\n", "three attempts, then the error")

        result = self.run_release("plan", pulls=False)
        self.assertIn("Bad credentials", result.stderr)
        self.assertFalse(slept.exists(), "an HTTP 4xx is GitHub's answer: not asked again")

    def test_nuget_ships_when_a_packages_folder_or_the_props_changed(self):
        for path in ("README.md", "dotnet/Laching.Core.Tests/A.cs", "dotnet/Fixture.App/A.cs", "Sources/LachingCore/A.swift"):
            self.change(path)
        self.assertEqual(self.out("nuget", "0.13.1"), "skip (the packages are unchanged since 0.13.0, their version on NuGet)")
        self.change("dotnet/Laching.Windows/Tray.cs")
        self.assertEqual(self.out("nuget", "0.13.1"), "ship (the packages changed since 0.13.0, their version on NuGet)")

    def test_the_web_packages_ship_when_their_sources_or_build_files_changed(self):
        self.assertEqual(self.out("web-projects").splitlines(), sorted(
            path.parent.name for path in (REPO / "web/src").glob("*/*.csproj")
            if "<IsPackable>true</IsPackable>" in path.read_text("utf8")))
        for path in ("web/README.md", "web/tests/Laching.Web.Jobs.Tests/A.cs", "web/scripts/a.sh", "dotnet/Laching.Core/A.cs"):
            self.change(path)
        self.assertEqual(self.out("web", "0.13.1"),
                         "skip (the web packages are unchanged since 0.13.0, their version on NuGet)")
        for path in ("web/src/Laching.Web.Jobs/Job.cs", "web/Directory.Packages.props"):
            self.git("reset", "-q", "--hard", "v0.13.0")
            self.change(path)
            self.assertEqual(self.out("web", "0.13.1"),
                             "ship (the web packages changed since 0.13.0, their version on NuGet)", path)

    def test_bump_writes_the_strings_and_lachingversion_only_when_nuget_ships(self):
        self.change("tools/a.py")
        self.assertEqual(self.out("bump", "0.13.1"), "Bumped 0.13.0 to 0.13.1; NuGet: skip (the packages are unchanged "
                                                     "since 0.13.0, their version on NuGet); web: skip (the web packages "
                                                     "are unchanged since 0.13.0, their version on NuGet)")
        self.assertIn('exact: "0.13.1"', self.read("README.md"))
        self.assertIn("latest release is `v0.13.1`", self.read("README.md"))
        self.assertIn("lachingVersion=0.13.1", self.read("kotlin/gradle.properties"))
        self.assertIn(">v0.13.1</a>", self.read("site/index.html"))
        self.assertIn(">0.13.0</LachingVersion>", self.read("dotnet/Directory.Build.props"), "NuGet keeps 0.13.0")
        self.assertIn(">0.13.0</LachingWebVersion>", self.read("web/Directory.Build.props"), "the web packages too")
        verify = subprocess.run(["bash", str(self.root / "scripts/verify.sh"), "--version", "0.13.1"], cwd=self.root,
                                capture_output=True, encoding="utf8")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertEqual(self.run_release("bump", "0.13.1").returncode, 1, "bump goes forward")
        self.commit("0.13.1: release")
        self.git("tag", "-a", "v0.13.1", "-m", "Laching 0.13.1")

        self.change("dotnet/Laching.Core/Clock.cs")
        self.assertIn("NuGet: ship", self.out("bump", "0.13.2"))
        self.assertIn(">0.13.2</LachingVersion>", self.read("dotnet/Directory.Build.props"))
        self.assertIn(">0.13.0</LachingWebVersion>", self.read("web/Directory.Build.props"), "each on its own")
        self.assertEqual(self.out("nuget", "0.13.2"), "ship (LachingVersion is 0.13.2 already)", "the same after")
        self.git("checkout", "-q", "--", ".")

        self.change("web/src/Laching.Web.Images/ImageEncoder.cs")
        self.assertIn("web: ship", self.out("bump", "0.13.2"))
        self.assertIn(">0.13.2</LachingWebVersion>", self.read("web/Directory.Build.props"))
        self.assertEqual(self.out("web", "0.13.2"), "ship (LachingWebVersion is 0.13.2 already)", "the same after")

    def test_landing_ships_when_it_changed_since_npms_version(self):
        self.assertEqual(self.out("landing"), "no (tools/landing is unchanged since 0.13.0, the version on npm)")
        self.change("tools/landing/src/index.ts")
        self.assertEqual(self.out("landing"), "yes (tools/landing changed since 0.13.0, the version on npm)")

    @staticmethod
    def job(name: str) -> str:
        """release.yml's job `name`, up to the next job."""
        return re.split(r"\n  [a-z-]+:\n", WORKFLOW.read_text("utf8").split(f"\n  {name}:\n", 1)[1], maxsplit=1)[0]

    def step(self, name: str, cwd: Path | None = None, job: str | None = None,
             **env: str) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
        """The release.yml step called `name` (in `job`, else the first), its script as the workflow has it, run in `cwd`
        (the checkout here) with the origin here: the result and the step's outputs."""
        lines = WORKFLOW.read_text("utf8").splitlines()
        first = lines.index(f"  {job}:") if job else 0
        start = next(n for n in range(first, len(lines)) if lines[n].endswith(f"name: {name}"))
        indent = lines[start].index("name:")
        start = lines.index(" " * indent + "run: |", start) + 1
        end = next(n for n in range(start, len(lines)) if lines[n].strip() and not lines[n].startswith(" " * (indent + 2)))
        output, summary = self.tmp / "output", self.tmp / "summary"
        output.write_text("", "utf8")
        variables = self.env(GITHUB_REPOSITORY="vmihalachi/laching", GITHUB_REF="refs/heads/master", BUMP="auto",
                             DRY_RUN="false", GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
        script = "\n".join(line[indent + 2:] for line in lines[start:end])
        result = subprocess.run(["bash", "-e", "-c", script], cwd=cwd or self.root, env={**variables, **env},
                                capture_output=True, encoding="utf8")
        return result, dict(line.split("=", 1) for line in output.read_text("utf8").splitlines())

    def plan(self, **env: str) -> tuple[subprocess.CompletedProcess, dict[str, str], str]:
        summary = self.tmp / "summary"
        summary.write_text("", "utf8")
        result, outputs = self.step("Plan (a dry run stops here)", **env)
        self.git("reset", "-q", "--hard")  # the runner's bump goes with the runner
        return result, outputs, summary.read_text("utf8")

    def checkout(self, origin: Path, commit: str, job: str) -> Path:
        """A fresh checkout of `commit`, as `job`'s actions/checkout makes it: the commit alone, without the tags, unless
        the job asks for `fetch-depth: 0` (the history and the tags)."""
        folder = self.tmp / f"job-{job}-{len(list(self.tmp.glob('job-*')))}"
        folder.mkdir()
        options = self.job(job).split("uses: actions/checkout@", 1)[1].split("\n      - ", 1)[0]
        refs = (["--tags", "+refs/heads/*:refs/remotes/origin/*"] if "fetch-depth: 0" in options
                else ["--depth", "1", "--no-tags", commit])
        for args in (["init", "-q"], ["remote", "add", "origin", origin.as_uri()], ["fetch", "-q", "origin", *refs],
                     ["checkout", "-q", "--detach", commit], ["config", "user.name", "fixture"],
                     ["config", "user.email", "fixture"]):
            subprocess.run(["git", "-C", str(folder), *args], check=True)
        return folder

    def origin(self) -> Path:
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        self.git("remote", "set-url", "origin", str(origin))
        self.git("push", "-q", "origin", "HEAD:refs/heads/master", "v0.13.0")
        return origin

    def remote(self, origin: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(origin), *args], capture_output=True, encoding="utf8").stdout.strip()

    def test_a_dry_run_writes_nothing_and_a_release_runs_on_master(self):
        origin = self.origin()
        self.change("dotnet/Laching.Windows/Tray.cs", pr=110)
        self.git("push", "-q", "origin", "HEAD:refs/heads/master")
        head = self.git("rev-parse", "HEAD")
        result, outputs, summary = self.plan(DRY_RUN="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(outputs, {"release": "false"})
        for line in ("Plan: release 0.13.1 (patch:", "NuGet: ship (", "Web packages: skip (", "@laching/landing: no (",
                     "Dry run: nothing"):
            self.assertIn(line, summary)
        self.assertEqual(self.remote(origin, "rev-parse", "master"), head)
        self.assertEqual(self.remote(origin, "tag", "--list", "v0.13.1"), "")
        refused, _, _ = self.plan(GITHUB_REF="refs/heads/feature")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("A release runs on master", refused.stdout)

    def test_the_tag_is_pushed_only_after_the_builds_with_the_tree_they_tested(self):
        # Nothing before the tag job writes to the repository, and the tag job waits for every build to pass.
        jobs = WORKFLOW.read_text("utf8").split("\n  tag:\n")
        self.assertNotIn("contents: write", jobs[0])
        self.assertIn("needs: [plan, core, windows, web, landing]", jobs[1].split("\n  npm:\n")[0])
        for job in ("core", "windows", "web", "landing"):
            self.assertIn(f"(needs.{job}.result == 'success' || needs.{job}.result == 'skipped')", jobs[1])

        origin = self.origin()
        self.change("dotnet/Laching.Core/Clock.cs", pr=110)
        self.git("push", "-q", "origin", "HEAD:refs/heads/master")
        head = self.git("rev-parse", "HEAD")
        result, plan, _ = self.plan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({key: plan[key] for key in ("release", "version", "previous", "base", "nuget", "web", "landing")},
                         {"release": "true", "version": "0.13.1", "previous": "0.13.0", "base": head, "nuget": "true",
                          "web": "false", "landing": "false"})
        self.assertEqual(self.remote(origin, "rev-parse", "master"), head, "the plan pushes nothing")
        self.assertEqual(self.remote(origin, "tag", "--list", "v0.13.1"), "")

        jobs = {"VERSION": plan["version"], "TREE": plan["tree"], "BASE": plan["base"]}
        for job in ("core", "windows", "web"):
            build, _ = self.step("Bump, as the release commit will be", self.checkout(origin, head, job), job, **jobs)
            self.assertEqual(build.returncode, 0, f"{job} bumps to the planned tree: " + build.stdout + build.stderr)

        # A failing pages dispatch strands nothing: the tag is out and the step's output written.
        result, tag = self.step("Commit the bump and tag it, in one push", self.checkout(origin, head, "tag"),
                                FAKE_GH_WORKFLOW_STATUS="1", **jobs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote(origin, "log", "-1", "--format=%s", "master"), "0.13.1: release")
        self.assertEqual(self.remote(origin, "rev-parse", "v0.13.1^{commit}"), tag["ref"])
        self.assertEqual(self.remote(origin, "rev-parse", "master"), tag["ref"])
        self.assertIn("workflow run pages.yml --ref master", (self.tmp / "pulls.log").read_text("utf8"))
        self.assertIn(">0.13.1</LachingVersion>", self.remote(origin, "show", "v0.13.1:dotnet/Directory.Build.props"))

        self.git("pull", "-q", "--tags", "origin", "master")
        result, outputs, summary = self.plan()
        self.assertEqual(outputs, {"release": "false"}, result.stdout + result.stderr)
        self.assertIn("Nothing to release: none (no commits since v0.13.1)", summary)

    def test_the_tag_job_pushes_nothing_when_master_moved_since_the_plan(self):
        origin = self.origin()
        self.change("docs/a.md", pr=110)
        self.git("push", "-q", "origin", "HEAD:refs/heads/master")
        head = self.git("rev-parse", "HEAD")
        _, plan, _ = self.plan()
        self.change("docs/b.md", pr=111)
        self.git("push", "-q", "origin", "HEAD:refs/heads/master")  # merged while the builds ran
        moved = self.git("rev-parse", "HEAD")
        result, outputs = self.step("Commit the bump and tag it, in one push", self.checkout(origin, head, "tag"),
                                    VERSION=plan["version"], TREE=plan["tree"], BASE=plan["base"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("master moved since the plan: nothing was pushed; dispatch the release again", result.stdout)
        self.assertEqual((self.remote(origin, "rev-parse", "master"), self.remote(origin, "tag", "--list", "v0.13.1")),
                         (moved, ""))
        self.assertEqual(outputs, {})

    def test_the_plan_never_moves_a_tag(self):
        origin = self.origin()
        self.change("docs/a.md", pr=111)
        self.git("push", "-q", "origin", "HEAD:refs/heads/master")
        self.git("tag", "v0.13.1", "HEAD~1")
        self.git("push", "-q", "origin", "v0.13.1")
        self.git("tag", "-d", "v0.13.1")
        result, outputs, _ = self.plan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("v0.13.1 already exists on origin", result.stdout)
        self.assertNotIn("release", outputs)


if __name__ == "__main__":
    unittest.main()
