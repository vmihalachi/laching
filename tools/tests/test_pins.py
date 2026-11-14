#!/usr/bin/env python3
"""Tests for companion.py's `bump`, `link` and `unlink`, over the fixture app and a Laching origin made here (git
repositories in a temporary folder; xcodegen, xcodebuild, npm and NuGet.org are stood in for), and for its wait for
NuGet.org (`wait_for_nuget`, `nuget-listed`), which reaches no network here."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import companion  # noqa: E402
import companion_ci  # noqa: E402
from support import BASH, NO_SHEBANG, NO_SHELL, TOOLS, WINDOWS, FixtureAppTestCase, git, shell  # noqa: E402

RESOLVED = "macos/FixtureCore/Package.resolved"
PROJECT_RESOLVED = "macos/Fixture.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
PBXPROJ = "macos/Fixture.xcodeproj/project.pbxproj"


def resolved(revision: str, version: str, origin_hash: str) -> str:
    """A Package.resolved as SwiftPM writes it, pinning Laching."""
    return (
        "{\n"
        f'  "originHash" : "{origin_hash}",\n'
        '  "pins" : [\n    {\n      "identity" : "laching",\n      "kind" : "remoteSourceControl",\n'
        '      "location" : "https://github.com/vmihalachi/laching",\n      "state" : {\n'
        f'        "revision" : "{revision}",\n        "version" : "{version}"\n'
        "      }\n    }\n  ],\n  \"version\" : 3\n}\n"
    )


class PinsTestCase(FixtureAppTestCase):
    """The fixture app at `self.root` and a Laching origin next to it, tagged v0.5.0 and v0.6.0. xcodegen and xcodebuild
    are stood in for (`self.calls` records them), npm has the versions in `self.npm`, and NuGet.org lists, for every
    package, each version of `self.nuget` from the second it names (None: it can't be reached). That clock, `self.now`,
    moves only when the wait sleeps, and `self.asked` are the packages NuGet.org was asked for."""

    def setUp(self):
        super().setUp()
        self.origin = self.root.parent / "laching-origin"
        self.origin.mkdir()
        (self.origin / "Package.swift").write_text('let package = Package(name: "Laching")\n', "utf8")
        for name in ("Laching.Core", "Laching.Companion", "Laching.Core.Tests"):
            project = self.origin / "dotnet" / name / f"{name}.csproj"
            project.parent.mkdir(parents=True)
            packable = "" if name.endswith("Tests") else "<IsPackable>true</IsPackable>"
            project.write_text(f"<Project><PropertyGroup>{packable}</PropertyGroup></Project>\n", "utf8")
        git(self.origin, "init", "-q")
        self.commits = {}
        for version in ("0.5.0", "0.6.0"):
            (self.origin / "release.txt").write_text(f"{version}\n", "utf8")
            git(self.origin, "add", "-A")
            git(self.origin, "commit", "-q", "-m", version)
            git(self.origin, "tag", "-a", f"v{version}", "-m", f"Laching {version}")
            self.commits[version] = git(self.origin, "rev-parse", "HEAD")
        self.calls: list[str] = []
        self.npm: list[str] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("regenerate_project", "regenerated"), ("resolve_packages", "resolved")):
            stack.enter_context(mock.patch.object(companion, name, side_effect=lambda config, n=name, v=value: (
                self.calls.append(n), v)[1]))
        stack.enter_context(mock.patch.object(companion, "xcode_resolves", return_value=False))
        stack.enter_context(mock.patch.object(companion, "npm_versions", side_effect=lambda package: self.npm))
        self.nuget: dict[str, float] | None = {"0.5.0": 0, "0.6.0": 0}
        self.asked: list[str] = []
        self.now = 0.0
        wait = companion.wait_for_nuget
        stack.enter_context(mock.patch.object(
            companion, "wait_for_nuget", side_effect=lambda packages, version, **options: wait(
                packages, version, fetch=self.nuget_versions, clock=lambda: self.now, sleep=self.sleep, **options)))

    def nuget_versions(self, package: str) -> list[str]:
        self.asked.append(package)
        if self.nuget is None:
            raise OSError("no route to nuget.org")
        return [version for version, since in self.nuget.items() if since <= self.now]

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = companion.main(list(argv))
        return code, out.getvalue()

    def files(self) -> dict[str, bytes]:
        """Every file of the app but git's own, with its bytes."""
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in sorted(self.root.rglob("*"))
                if path.is_file() and ".git" not in path.relative_to(self.root).parts}


class BumpTests(PinsTestCase):
    def setUp(self):
        super().setUp()
        git(self.root.parent, "clone", "-q", str(self.origin), str(self.root / "laching"))
        git(self.root / "laching", "checkout", "-q", "v0.5.0")
        manifest = (self.root / "macos/FixtureCore/Package.swift").read_bytes()
        (self.root / RESOLVED).write_text(
            resolved(self.commits["0.5.0"], "0.5.0", hashlib.sha256(manifest).hexdigest()), "utf8")
        (self.root / PROJECT_RESOLVED).parent.mkdir(parents=True)
        (self.root / PROJECT_RESOLVED).write_text(resolved(self.commits["0.5.0"], "0.5.0", "f" * 64), "utf8")

    def test_bump_moves_every_pin(self):
        before = self.files()
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        config = companion.load()
        self.assertTrue(companion.pins_agree(companion.pins(config)))
        self.assertEqual({version.removeprefix("v") for _, version in companion.pins(config)}, {"0.6.0"})
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.6.0")
        self.assertEqual(config.get("laching.tag"), "v0.6.0")
        after = self.files()
        # Only the versions changed: each file differs from its old self by the version strings alone.
        for path in ("companion.json", "macos/project.yml", "macos/FixtureCore/Package.swift",
                     "windows/Fixture.Core/Fixture.Core.csproj"):
            self.assertEqual(after[path].replace(b"0.6.0", b"0.5.0"), before[path], path)
        core = json.loads(after[RESOLVED])
        self.assertEqual(core["pins"][0]["state"], {"revision": self.commits["0.6.0"], "version": "0.6.0"})
        self.assertEqual(core["originHash"], hashlib.sha256(after["macos/FixtureCore/Package.swift"]).hexdigest())
        project = json.loads(after[PROJECT_RESOLVED])
        self.assertEqual(project["pins"][0]["state"], {"revision": self.commits["0.6.0"], "version": "0.6.0"})
        self.assertEqual(project["originHash"], "f" * 64, "the project's hash covers its path: left to Xcode")
        self.assertIn("originHash", out)
        self.assertEqual(self.calls, ["regenerate_project"])

    def test_bump_moves_the_reusable_macos_workflow_pin(self):
        workflow = self.root / ".github/workflows/macos.yml"
        workflow.write_text("jobs:\n  test:\n    uses: vmihalachi/laching/.github/workflows/macos-app.yml@v0.5.0\n", "utf8")
        self.assertIn((".github/workflows/macos.yml macos-app.yml@", "v0.5.0"), companion.pins(companion.load()))
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertIn("macos-app.yml@v0.6.0", workflow.read_text("utf8"))
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))

    def test_bump_moves_the_reusable_windows_core_workflow_pin(self):
        workflow = self.root / ".github/workflows/windows-core.yml"
        workflow.write_text("jobs:\n  test:\n    uses: vmihalachi/laching/.github/workflows/windows-core.yml@v0.5.0\n", "utf8")
        self.assertIn((".github/workflows/windows-core.yml windows-core.yml@", "v0.5.0"), companion.pins(companion.load()))
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertIn("windows-core.yml@v0.6.0", workflow.read_text("utf8"))
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))

    def test_bump_moves_the_reusable_linux_workflow_pins(self):
        workflows = self.root / ".github/workflows"
        core, vectors = workflows / "core.yml", workflows / "regenerate-vectors.yml"
        core.write_text("jobs:\n  test:\n    uses: vmihalachi/laching/.github/workflows/core-linux.yml@v0.5.0\n", "utf8")
        vectors.write_text("jobs:\n  regenerate:\n"
                           "    uses: vmihalachi/laching/.github/workflows/vectors-linux.yml@v0.5.0\n", "utf8")
        found = companion.pins(companion.load())
        self.assertIn((".github/workflows/core.yml core-linux.yml@", "v0.5.0"), found)
        self.assertIn((".github/workflows/regenerate-vectors.yml vectors-linux.yml@", "v0.5.0"), found)
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertIn("core-linux.yml@v0.6.0", core.read_text("utf8"))
        self.assertIn("vectors-linux.yml@v0.6.0", vectors.read_text("utf8"))
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))

    def test_bump_moves_a_branch_ref_with_slashes_whole(self):
        workflow = self.root / ".github/workflows/core.yml"
        workflow.write_text("jobs:\n  test:\n    uses: vmihalachi/laching/.github/workflows/core-linux.yml"
                            "@users/vlad/linux-ci  # until the release\n", "utf8")
        self.assertIn((".github/workflows/core.yml core-linux.yml@", "users/vlad/linux-ci"), companion.pins(companion.load()))
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(workflow.read_text("utf8"), "jobs:\n  test:\n    uses: vmihalachi/laching/.github/workflows/"
                         "core-linux.yml@v0.6.0  # until the release\n")

    def test_an_android_app_pins_the_submodules_kotlin_build(self):
        manifest = self.root / "companion.json"
        data = json.loads(manifest.read_text("utf8"))
        data["android"] = {"dir": "android"}
        manifest.write_text(json.dumps(data), "utf8")
        found = dict(companion.pins(companion.load()))
        self.assertNotIn("laching/kotlin/gradle.properties lachingVersion", found, "a release before the Kotlin side")
        (self.root / "laching" / "kotlin").mkdir()
        (self.root / "laching" / "kotlin" / "gradle.properties").write_text(
            "org.gradle.jvmargs=-Xmx2g\nlachingVersion=0.4.0\n", "utf8")
        found = dict(companion.pins(companion.load()))
        self.assertEqual(found["laching/kotlin/gradle.properties lachingVersion"], "0.4.0")
        self.assertFalse(companion.pins_agree(list(found.items())))

    def release(self, version: str, nuget: str) -> None:
        """Tags `version` on the origin, its NuGet packages at `nuget` (LachingVersion)."""
        (self.origin / "dotnet" / "Directory.Build.props").write_text(
            f"<Project><PropertyGroup><LachingVersion Condition=\"'$(LachingVersion)' == ''\">{nuget}</LachingVersion>"
            "</PropertyGroup></Project>\n", "utf8")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", version)
        git(self.origin, "tag", "-a", f"v{version}", "-m", f"Laching {version}")

    def test_the_nuget_pins_take_the_releases_nuget_version(self):
        self.release("0.7.0", "0.6.0")  # the packages didn't change in 0.7.0
        code, out = self.cli("bump", "0.7.0")
        self.assertEqual(code, 0, out)
        csproj = self.root / "windows/Fixture.Core/Fixture.Core.csproj"
        text = csproj.read_text("utf8")
        self.assertIn('"Laching.Core" Version="0.6.0"', text)
        self.assertIn('"Laching.Companion" Version="0.6.0"', text)
        self.assertIn("v0.7.0                   windows/Fixture.Core/Fixture.Core.csproj Laching.Companion 0.6.0 "
                      "(NuGet's version in v0.7.0)", out)
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))

        csproj.write_text(text.replace('"Laching.Companion" Version="0.6.0"', '"Laching.Companion" Version="0.7.0"'))
        code, out = self.cli("pins")
        self.assertEqual(code, 1, "the NuGet pins disagree with each other")
        self.assertIn("0.7.0, not 0.6.0", out)

    def test_bump_to_the_current_version_changes_nothing(self):
        before = self.files()
        code, out = self.cli("bump", "v0.5.0")
        self.assertEqual(code, 0, out)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.calls, [])

    def test_xcode_rewrites_the_projects_package_resolved(self):
        def resolve(config):
            self.calls.append("resolve_packages")
            (self.root / PROJECT_RESOLVED).write_text(resolved(self.commits["0.6.0"], "0.6.0", "a" * 64), "utf8")
            return "resolved"

        with mock.patch.object(companion, "xcode_resolves", return_value=True), \
                mock.patch.object(companion, "resolve_packages", side_effect=resolve):
            code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls, ["regenerate_project", "resolve_packages"])
        self.assertEqual(json.loads((self.root / PROJECT_RESOLVED).read_text("utf8"))["originHash"], "a" * 64)
        self.assertIn(f"changed  {PROJECT_RESOLVED}", out)

    def test_bump_refuses_a_tag_origin_doesnt_have(self):
        before = self.files()
        for version in ("0.7.0", "0.6"):
            code, out = self.cli("bump", version)
            self.assertEqual(code, 1)
            self.assertIn("release it first" if version == "0.7.0" else "isn't a version", out)
        self.assertEqual(self.files(), before)
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.5.0")

    def test_bump_refuses_a_submodule_with_changes(self):
        (self.root / "laching" / "release.txt").write_text("mine\n", "utf8")
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 1)
        self.assertIn("uncommitted changes", out)
        self.assertEqual(companion.load().get("laching.tag"), "v0.5.0")

    def test_bump_keeps_crlf_line_endings(self):
        csproj = self.root / "windows/Fixture.Core/Fixture.Core.csproj"
        csproj.write_bytes(csproj.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.cli("bump", "0.6.0")
        text = csproj.read_bytes()
        self.assertIn(b'Version="0.6.0"', text)
        self.assertEqual(text.count(b"\n"), text.count(b"\r\n"))

    def landing(self, tool: str | None = None) -> Path:
        """A landing page on @laching/landing 0.4.0, its lockfile and, with `tool`, a landing tool at that path."""
        landing = self.root / "landing"
        landing.mkdir()
        (landing / "package.json").write_text('{\n  "devDependencies": {\n    "@laching/landing": "0.4.0"\n  }\n}\n')
        (landing / "package-lock.json").write_text('{ "lock": "0.4.0" }\n', "utf8")
        data = json.loads((self.root / "companion.json").read_text("utf8"))
        data["landing"] = {"dir": "landing", **({"tool": tool} if tool else {})}
        (self.root / "companion.json").write_text(json.dumps(data, indent=2) + "\n", "utf8")
        return landing

    def test_the_landing_page_takes_npms_newest_at_or_below_the_release(self):
        landing = self.landing()
        self.npm = ["0.0.0-stage", "0.4.0", "0.5.0", "0.5.1", "0.7.0"]
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertIn('"@laching/landing": "0.5.1"', (landing / "package.json").read_text("utf8"))
        self.assertIn("`npm install` in landing", out, "no landing tool: the lockfile is left to you")
        self.assertIn("0.5.1                    landing/package.json @laching/landing", out)
        self.npm = ["0.4.0"]
        code, out = self.cli("bump", "0.6.0")
        self.assertIn("@laching/landing stays at 0.5.1", out)

    @unittest.skipIf(WINDOWS, NO_SHEBANG)
    def test_the_landing_tool_updates_the_lockfile_or_everything_goes_back(self):
        tool = self.root.parent / "fake-vp"
        landing = self.landing(str(tool))
        self.npm = ["0.4.0", "0.6.0"]
        (landing / "pnpm-workspace.yaml").write_text("minimumReleaseAgeExclude: []\n", "utf8")
        git(landing, "init", "-q")
        git(landing, "add", "-A")
        git(landing, "commit", "-q", "-m", "landing")
        tool.write_text('#!/bin/sh\n[ "$1" = install ] && echo \'{ "lock": "0.6.0" }\' > package-lock.json'
                        ' && echo "minimumReleaseAgeExclude: [0.6.0]" > pnpm-workspace.yaml\n', "utf8")
        tool.chmod(0o755)
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertIn("0.6.0", (landing / "package-lock.json").read_text("utf8"))
        self.assertIn("changed  landing/package-lock.json (`", out)
        self.assertIn("changed  landing/pnpm-workspace.yaml (`", out)
        git(landing, "commit", "-q", "-am", "0.6.0")

        self.cli("bump", "0.5.0")  # npm has 0.4.0 at or below it, older than the page's: it stays
        self.assertIn('"0.6.0"', (landing / "package.json").read_text("utf8"))
        (landing / "package.json").write_text('{ "devDependencies": { "@laching/landing": "0.4.0" } }\n', "utf8")
        (landing / "package-lock.json").write_text('{ "lock": "0.4.0" }\n', "utf8")
        tool.write_text("#!/bin/sh\necho 'no network' >&2\nexit 1\n", "utf8")
        code, out = self.cli("bump", "0.6.0")
        self.assertIn("landing has uncommitted dependency changes, so the lockfile is yours", out)
        (landing / "package.json").write_text('{ "devDependencies": { "@laching/landing": "0.4.0" } }\n', "utf8")
        git(landing, "commit", "-q", "-am", "back to 0.4.0")
        code, out = self.cli("bump", "0.6.0")
        self.assertIn("failed, so @laching/landing stays at 0.4.0", out)
        self.assertIn('"0.4.0"', (landing / "package.json").read_text("utf8"))
        self.assertIn('"0.4.0"', (landing / "package-lock.json").read_text("utf8"))

    def test_bump_waits_until_nuget_lists_the_packages_then_moves(self):
        self.nuget["0.6.0"] = 400  # listed 6:40 after the bump starts
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertIn("Waiting for NuGet.org to list Laching.Companion, Laching.Core at 0.6.0", out)
        self.assertIn("nothing is written until then (`bump --no-wait` doesn't ask)", out)
        self.assertIn("  3:00  still waiting for Laching.Companion, Laching.Core\n", out)
        self.assertIn("  6:45  NuGet.org lists them\n", out)
        self.assertLess(out.index("NuGet.org lists them"), out.index("Laching v0.6.0:"), "the wait, then the move")
        self.assertEqual(set(self.asked), {"Laching.Core", "Laching.Companion"})
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))
        self.assertIn(b'"Laching.Core" Version="0.6.0"', self.files()["windows/Fixture.Core/Fixture.Core.csproj"])
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.6.0")

    def test_bump_gives_up_after_fifteen_minutes_with_nothing_written(self):
        del self.nuget["0.6.0"]
        before = self.files()
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 1)
        self.assertIn("companion bump: NuGet.org doesn't list Laching.Companion, Laching.Core at 0.6.0 after 15:00", out)
        for line in ("Nothing was written", "--no-wait", "dotnet nuget locals http-cache --clear"):
            self.assertIn(line, out)
        self.assertEqual(self.now, 15 * 60)
        self.assertEqual(self.files(), before, "not half moved: no pin, and the submodule's files as they were")
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.5.0")
        self.assertEqual(git(self.root / "laching", "status", "--porcelain"), "")
        self.assertEqual(self.calls, [])
        self.nuget["0.6.0"] = 0  # listed at last: the same command moves the app
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.6.0")

    def test_bump_stopped_during_the_wait_has_written_nothing(self):
        del self.nuget["0.6.0"]
        before = self.files()

        def interrupted(seconds: float) -> None:  # Ctrl-C while it sleeps between two asks
            raise KeyboardInterrupt

        self.sleep = interrupted
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 1)
        self.assertIn("companion bump: stopped while waiting for NuGet.org. Nothing was written.", out)
        self.assertEqual(self.files(), before)
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.5.0")

    def test_bump_stops_at_once_when_nuget_cant_be_asked_unless_told_not_to_ask(self):
        self.nuget = None
        before = self.files()
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 1)
        self.assertIn("couldn't ask NuGet.org whether it lists Laching.Companion 0.6.0: no route to nuget.org", out)
        self.assertIn("Nothing was written", out)
        self.assertEqual(self.now, 0, "offline is no reason to wait")
        self.assertEqual(self.files(), before)
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.5.0")
        self.asked.clear()
        code, out = self.cli("bump", "--no-wait", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked, [])
        self.assertIn("note     NuGet.org wasn't asked whether it lists Laching.Companion, Laching.Core at 0.6.0 "
                      "(--no-wait): a restore fails until it does", out)
        self.assertTrue(companion.pins_agree(companion.pins(companion.load())))
        self.assertEqual(self.cli("bump", "0.5.0", "--no-wait")[0], 0, "the flag after the version too")
        self.assertEqual(self.asked, [])
        for argv in (("bump", "--no-wait"), ("bump", "--no-wait", "--no-wait", "0.6.0"), ("bump", "0.6.0", "0.5.0")):
            self.assertEqual(self.cli(*argv)[0], 2, argv)
        self.assertEqual(companion.load().get("laching.tag"), "v0.5.0")

    def test_bump_wait_only_waits_once_for_every_app_and_moves_none(self):
        self.nuget["0.6.0"] = 400
        before, elsewhere = self.files(), self.root.parent / "not-an-app"
        elsewhere.mkdir()
        code, out = self.cli("bump", "--wait-only", "0.6.0", str(self.root), str(elsewhere), str(self.root))
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("Waiting for NuGet.org to list Laching.Companion, Laching.Core at 0.6.0"), 1, out)
        self.assertIn("; no app moves until then (`bump-apps.sh --no-wait` doesn't ask)", out)
        self.assertIn("  6:45  NuGet.org lists them\n", out)
        self.assertEqual(self.files(), before, "it only waits")
        self.assertEqual(git(self.root / "laching", "describe", "--tags"), "v0.5.0")
        self.asked.clear()
        code, out = self.cli("bump", "0.6.0")  # each bump then asks once and goes on
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(self.asked), ["Laching.Companion", "Laching.Core"])
        self.assertNotIn("Waiting for", out)

    def test_bump_wait_only_gives_up_once_and_leaves_refused_apps_to_their_bump(self):
        del self.nuget["0.6.0"]
        before = self.files()
        code, out = self.cli("bump", "--wait-only", "0.6.0", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("companion bump: NuGet.org doesn't list Laching.Companion, Laching.Core at 0.6.0 after 15:00\n"
                      "No app moved. Run it again once NuGet.org lists them, or with --no-wait", out)
        self.assertEqual((self.now, self.files()), (15 * 60, before))
        self.asked.clear()
        self.assertEqual(self.cli("bump", "--wait-only", "0.7.0", str(self.root)), (0, ""), "no such tag: bump says so")
        self.assertEqual(self.asked, [])
        self.assertIn("'0.6' isn't a version", self.cli("bump", "--wait-only", "0.6", str(self.root))[1])
        for argv in (("bump", "--wait-only"), ("bump", "--wait-only", "--no-wait", "0.6.0")):
            self.assertEqual(self.cli(*argv)[0], 2, argv)

    def test_nuget_is_asked_for_the_packages_own_version_and_only_when_a_pin_moves(self):
        self.release("0.7.0", "0.6.0")  # the tag is v0.7.0 and its packages are still 0.6.0, which the app doesn't pin yet
        self.nuget = {"0.6.0": 30}
        code, out = self.cli("bump", "0.7.0")
        self.assertEqual(code, 0, out)
        self.assertIn("Waiting for NuGet.org to list Laching.Companion, Laching.Core at 0.6.0", out)
        self.assertEqual(self.now, 30)
        (self.origin / "release.txt").write_text("0.8.0\n", "utf8")
        self.release("0.8.0", "0.6.0")  # nor in 0.8.0: no NuGet pin moves, so nothing is asked
        self.nuget, self.asked = None, []
        code, out = self.cli("bump", "0.8.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked, [])
        self.assertNotIn("NuGet.org", out)
        self.assertEqual(companion.load().get("laching.tag"), "v0.8.0")

    def test_an_app_without_nuget_pins_asks_nothing(self):
        self.nuget = None
        csproj = self.root / "windows/Fixture.Core/Fixture.Core.csproj"
        csproj.write_text(companion._NUGET.sub("", csproj.read_text("utf8")), "utf8")
        self.assertFalse([pin for pin in companion.pin_sites(companion.load()) if pin.package])
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked, [])
        self.assertEqual(companion.load().get("laching.tag"), "v0.6.0")

    def bump_apps(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "COMPANION_ROOT" and not key.startswith("GIT_")}
        return subprocess.run(["bash", str(TOOLS / "bump-apps.sh"), *args],
                              capture_output=True, encoding="utf8", env={**env, **extra})

    def test_bump_apps_bumps_then_verifies_each_app(self):
        failing = self.bump_apps("0.7.0", str(self.root))
        self.assertEqual(failing.returncode, 1)
        self.assertIn("v0.7.0 isn't a tag", failing.stderr)
        self.assertIn("bump failed    app", failing.stdout)
        passing = self.bump_apps("0.5.0", str(self.root))
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn("ok    Laching pins agree", passing.stdout)
        self.assertIn("ok             app", passing.stdout)
        self.assertEqual(self.bump_apps().returncode, 2)

    @unittest.skipIf(WINDOWS, NO_SHEBANG)
    def test_bump_apps_waits_once_first_or_passes_no_wait_on(self):
        # A python3 that notes what it is started with, then is this one; with $GIVE_UP, `bump --wait-only` fails.
        fakes, log = self.root.parent / "fakes", self.root.parent / "python3.log"
        fakes.mkdir()
        (fakes / "python3").write_text(
            f'#!/bin/sh\necho "$*" >> "{log}"\ncase " $* " in *" --wait-only "*) [ -z "$GIVE_UP" ] || exit 3 ;; esac\n'
            f'exec "{sys.executable}" "$@"\n', "utf8")
        (fakes / "python3").chmod(0o755)
        path = f"{fakes}{os.pathsep}{os.environ.get('PATH', '')}"
        for args in (("--no-wait", "0.5.0", str(self.root)), ("0.5.0", "--no-wait", str(self.root)),
                     ("0.5.0", str(self.root), "--no-wait")):
            log.write_text("", "utf8")
            result = self.bump_apps(*args, PATH=path)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("ok             app", result.stdout)
            self.assertIn("companion.py bump --no-wait 0.5.0\n", log.read_text("utf8"), args)
            self.assertNotIn("--wait-only", log.read_text("utf8"), args)
        log.write_text("", "utf8")
        self.assertEqual(self.bump_apps("0.5.0", str(self.root), PATH=path).returncode, 0)
        calls, tool = log.read_text("utf8").splitlines(), TOOLS / "companion.py"
        self.assertEqual([call for call in calls if " bump " in call],
                         [f"{tool} bump --wait-only 0.5.0 {self.root}", f"{tool} bump 0.5.0"],
                         "one wait for every app first, then each bump")
        log.write_text("", "utf8")
        given_up = self.bump_apps("0.5.0", str(self.root), PATH=path, GIVE_UP="1")
        self.assertEqual(given_up.returncode, 3, "the wait's own status")
        self.assertNotIn("== app", given_up.stdout)
        self.assertNotIn("bump 0.5.0", log.read_text("utf8"))
        usage = self.bump_apps("--no-wait")
        self.assertEqual(usage.returncode, 2)
        self.assertIn("bump-apps.sh --no-wait 0.9.1", usage.stderr)
        self.assertIn("bump-apps.sh --report", self.bump_apps("--help").stdout, "the usage's last line")

    def test_bump_apps_finds_the_apps_next_to_its_checkout_but_not_worktrees(self):
        tools = self.root.parent / "checkout" / "tools"
        tools.mkdir(parents=True)
        for name in ("bump-apps.sh", "companion.py", "companion.schema.json", "conform.py"):
            (tools / name).write_bytes((TOOLS / name).read_bytes())
        worktree = self.root.parent / "app-worktree"
        worktree.mkdir()
        (worktree / "companion.json").write_text("{}", "utf8")
        (worktree / ".git").write_text("gitdir: elsewhere\n", "utf8")
        result = subprocess.run(["bash", str(tools / "bump-apps.sh"), "0.7.0"], capture_output=True, encoding="utf8",
                                env={key: value for key, value in os.environ.items() if key != "COMPANION_ROOT"})
        self.assertIn("Skipping app-worktree: a git worktree", result.stdout)
        self.assertIn("bump failed    app", result.stdout)
        self.assertNotIn("== app-worktree", result.stdout)


class NpmVersionsTests(unittest.TestCase):
    def test_npm_runs_from_the_path_which_finds(self):
        # On Windows that's npm.cmd, which subprocess can't start by its bare name.
        found = str(Path("C:/Program Files/nodejs/npm.cmd"))
        answer = subprocess.CompletedProcess([], 0, stdout='["0.19.8", "0.19.9"]', stderr="")
        with mock.patch.object(companion.shutil, "which", return_value=found),                 mock.patch.object(companion.subprocess, "run", return_value=answer) as run:
            self.assertEqual(companion.npm_versions("@laching/landing"), ["0.19.8", "0.19.9"])
        self.assertEqual(run.call_args.args[0][0], found)

    def test_a_missing_or_broken_npm_is_a_pin_error(self):
        with mock.patch.object(companion.shutil, "which", return_value=None):
            self.assertRaisesRegex(companion.PinError, "isn't installed", companion.npm_versions, "@laching/landing")
        with mock.patch.object(companion.shutil, "which", return_value="npm"),                 mock.patch.object(companion.subprocess, "run", side_effect=FileNotFoundError(2, "not found")):
            self.assertRaisesRegex(companion.PinError, "couldn't run npm", companion.npm_versions, "@laching/landing")


class NuGetWaitTests(unittest.TestCase):
    """`wait_for_nuget` and the `nuget-listed` command over a NuGet.org made here: it lists 1.1.0 of every package, and
    1.2.0 of a package from the second `self.since` names. Between the two seconds of `self.down` a request raises
    `self.error` (no answer). The clock, `self.now`, moves only when the wait sleeps; `self.asked` is each request, with
    its second."""

    def setUp(self):
        self.now = 0.0
        self.since: dict[str, float] = {}
        self.down = (0.0, 0.0)
        self.error: Exception = OSError("timed out")
        self.asked: list[tuple[float, str]] = []
        self.said: list[str] = []

    def fetch(self, package: str) -> list[str]:
        self.asked.append((self.now, package))
        if self.down[0] <= self.now < self.down[1]:
            raise self.error
        return ["1.1.0", *(["1.2.0"] if self.since.get(package, float("inf")) <= self.now else [])]

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, *packages: str, **options) -> None:
        companion.wait_for_nuget(list(packages), "1.2.0", fetch=self.fetch, clock=lambda: self.now, sleep=self.sleep,
                                 say=self.said.append, **options)

    def cli(self, *argv: str) -> tuple[int, str]:
        wait, out = companion.wait_for_nuget, io.StringIO()
        with mock.patch.object(companion, "wait_for_nuget", side_effect=lambda packages, version, **options: wait(
                packages, version, fetch=self.fetch, clock=lambda: self.now, sleep=self.sleep, **options)), \
                mock.patch.object(companion, "load", side_effect=AssertionError("it needs no companion.json")), \
                redirect_stdout(out), redirect_stderr(out):
            code = companion.main(list(argv))
        return code, out.getvalue()

    def test_packages_listed_already_are_asked_for_once_and_nothing_is_said(self):
        self.since = {"Laching.Core": 0, "Laching.Windows": 0}
        self.wait("Laching.Windows", "Laching.Core", "Laching.Core")
        self.assertEqual(self.asked, [(0, "Laching.Core"), (0, "Laching.Windows")])
        self.assertEqual((self.said, self.now), ([], 0))

    def test_it_asks_every_fifteen_seconds_for_what_is_still_missing_and_says_so(self):
        self.since = {"Laching.Core": 0, "Laching.Web.Jobs": 100}
        self.wait("Laching.Core", "Laching.Web.Jobs", note="; the note")
        self.assertEqual(self.asked, [(0, "Laching.Core"), *((second, "Laching.Web.Jobs") for second in range(0, 120, 15))])
        self.assertEqual(self.said, [
            "Waiting for NuGet.org to list Laching.Web.Jobs at 1.2.0: a version appears some minutes after its push, "
            "and this gives up after 15:00; the note",
            "  1:00  still waiting for Laching.Web.Jobs",
            "  1:45  NuGet.org lists them"])

    def test_it_gives_up_after_fifteen_minutes_naming_what_is_missing(self):
        self.since = {"Laching.Core": 30}
        with self.assertRaisesRegex(companion.PinError, r"^NuGet\.org doesn't list Laching\.Windows at 1\.2\.0 after 15:00$"):
            self.wait("Laching.Core", "Laching.Windows")
        self.assertEqual(self.now, 15 * 60)
        self.assertEqual(self.asked[-1], (15 * 60, "Laching.Windows"), "asked a last time when the time is up")
        self.assertEqual([line for line in self.said if "still waiting" in line][-1],
                         " 14:00  still waiting for Laching.Windows")
        with self.assertRaisesRegex(companion.PinError, "after 0:40"):
            self.wait("Laching.Windows", timeout=40, interval=20)

    def test_a_request_that_fails_is_made_again_once_nuget_has_answered(self):
        self.since, self.down = {"Laching.Core": 60}, (15, 45)
        self.wait("Laching.Core")
        self.assertEqual([second for second, _ in self.asked], [0, 15, 30, 45, 60])
        self.since, self.down, self.now = {}, (15, 10_000), 0
        with self.assertRaisesRegex(companion.PinError, "after 15:00; its last answer for Laching.Core: timed out"):
            self.wait("Laching.Core")
        self.since, self.down, self.now = {}, (15, 45), 0  # it answered again since: that failure is history
        with self.assertRaisesRegex(companion.PinError,
                                    r"^NuGet\.org doesn't list Laching\.Core at 1\.2\.0 after 15:00$"):
            self.wait("Laching.Core")

    def test_nuget_out_of_reach_from_the_start_stops_at_once_unless_patient(self):
        self.since, self.down = {"Laching.Core": 0}, (0, 31)
        with self.assertRaisesRegex(companion.PinError,
                                    r"couldn't ask NuGet\.org whether it lists Laching\.Core 1\.2\.0: timed out"):
            self.wait("Laching.Core")
        self.assertEqual((self.now, self.said), (0, []))
        self.wait("Laching.Core", patient=True)
        self.assertEqual(self.now, 45)
        self.error, self.now = urllib.error.URLError("no route"), 0
        with self.assertRaisesRegex(companion.PinError, "couldn't ask NuGet.org whether it lists Laching.Core 1.2.0: "
                                                        "<urlopen error no route>"):
            self.wait("Laching.Core")
        self.assertEqual(self.now, 0)

    def test_an_error_status_or_a_strange_answer_came_from_nuget_so_it_waits_through_it(self):
        for error in (urllib.error.HTTPError("url", 503, "Unavailable", None, None),
                      urllib.error.HTTPError("url", 429, "Too Many Requests", None, None), ValueError("not JSON")):
            self.since, self.down, self.error, self.now = {"Laching.Core": 0}, (0, 31), error, 0
            self.wait("Laching.Core")
            self.assertEqual(self.now, 45, error)

    def test_nuget_never_answering_isnt_it_saying_the_packages_are_missing(self):
        self.down = (0, 10_000)
        with self.assertRaisesRegex(companion.PinError, r"^couldn't reach NuGet\.org to ask whether it lists "
                                                        r"Laching\.Core at 1\.2\.0 in 15:00; its last answer for "
                                                        r"Laching\.Core: timed out$"):
            self.wait("Laching.Core", patient=True)
        self.error, self.now = urllib.error.HTTPError("url", 503, "Unavailable", None, None), 0
        with self.assertRaisesRegex(companion.PinError, r"^couldn't reach NuGet\.org .* in 15:00; its last answer for "
                                                        r"Laching\.Core: HTTP Error 503: Unavailable$"):
            self.wait("Laching.Core")

    def test_a_version_is_found_whatever_its_case(self):
        companion.wait_for_nuget(["Laching.Core"], "1.2.0-Beta", fetch=lambda package: ["1.2.0-beta"],
                                 clock=lambda: 0, sleep=self.sleep, say=self.said.append)
        self.assertEqual(self.now, 0)

    def test_nuget_versions_reads_the_flat_containers_index_by_the_lowercase_id(self):
        import urllib.request

        with mock.patch.object(urllib.request, "urlopen",
                               return_value=io.BytesIO(b'{"versions": ["0.22.0", "0.23.0"]}')) as urlopen:
            self.assertEqual(companion.nuget_versions("Laching.Core"), ["0.22.0", "0.23.0"])
        self.assertEqual(urlopen.call_args.args[0], "https://api.nuget.org/v3-flatcontainer/laching.core/index.json")
        missing = urllib.error.HTTPError("url", 404, "Not Found", None, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=missing):
            self.assertEqual(companion.nuget_versions("Laching.New"), [], "never listed: nothing yet, not an error")
        for error in (urllib.error.HTTPError("url", 503, "Unavailable", None, None), urllib.error.URLError("no route"),
                      TimeoutError("timed out")):
            with mock.patch.object(urllib.request, "urlopen", side_effect=error):
                self.assertRaises(OSError, companion.nuget_versions, "Laching.Core")
        for answer in (b"<html>", b"[]", b'{"count": 2}'):
            with mock.patch.object(urllib.request, "urlopen", return_value=io.BytesIO(answer)):
                self.assertRaises(ValueError, companion.nuget_versions, "Laching.Core")

    def test_the_nuget_listed_command_waits_through_a_nuget_out_of_reach(self):
        self.since, self.down = {"Laching.Core": 300, "Laching.Web.Jobs": 400}, (0, 31)
        code, out = self.cli("nuget-listed", "1.2.0", "Laching.Core", "Laching.Web.Jobs")
        self.assertEqual(code, 0, out)
        self.assertIn("Waiting for NuGet.org to list Laching.Core, Laching.Web.Jobs at 1.2.0", out)
        self.assertIn("  5:00  still waiting for Laching.Web.Jobs\n", out)
        self.assertTrue(out.endswith("  6:45  NuGet.org lists them\n"
                                     "NuGet.org lists Laching.Core, Laching.Web.Jobs at 1.2.0.\n"), out)

    def test_the_nuget_listed_command_fails_after_fifteen_minutes(self):
        self.since = {"Laching.Core": 0}
        code, out = self.cli("nuget-listed", "1.2.0", "Laching.Core", "Laching.Web.Jobs")
        self.assertEqual(code, 1)
        self.assertIn("companion nuget-listed: NuGet.org doesn't list Laching.Web.Jobs at 1.2.0 after 15:00", out)
        self.assertEqual(self.now, 15 * 60)
        for argv in (("nuget-listed",), ("nuget-listed", "1.2.0"), ("nuget-listed", "Laching.Core", "1.2.0")):
            self.assertEqual(self.cli(*argv)[0], 2, argv)


class LinkTests(PinsTestCase):
    """The app is a git repository (committed) and the Laching checkout sits next to it, at ../laching. Not on a CI
    runner, whatever runs these tests: there a link fails the pins as it does for a commit."""

    def setUp(self):
        super().setUp()
        local = mock.patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""})
        local.start()
        self.addCleanup(local.stop)
        self.checkout = self.root.parent / "laching"
        git(self.root.parent, "clone", "-q", str(self.origin), str(self.checkout))
        for path, text in ((RESOLVED, resolved(self.commits["0.5.0"], "0.5.0", "c" * 64)),
                           (PBXPROJ, "// the project XcodeGen wrote\n")):
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, "utf8")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "app")

    def status(self) -> str:
        return git(self.root, "status", "--porcelain", "--untracked-files=all")

    def test_link_switches_every_side_and_unlink_puts_back_every_byte(self):
        code, out = self.cli("link")
        self.assertEqual(code, 0, out)
        self.assertIn("path: ../../laching", (self.root / "macos/project.yml").read_text("utf8"))
        self.assertNotIn("exactVersion", (self.root / "macos/project.yml").read_text("utf8"))
        self.assertIn('.package(name: "laching", path: "../../../laching")',
                      (self.root / "macos/FixtureCore/Package.swift").read_text("utf8"))
        targets = (self.root / "windows/Directory.Build.targets").read_text("utf8")
        self.assertIn('<_LachingLinkable Include="Laching.Companion;Laching.Core" />', targets)
        self.assertIn("$(MSBuildThisFileDirectory)../../laching/dotnet/", targets)
        self.assertEqual(companion.linked_dotnet(companion.load(self.root)), "../../laching/dotnet/", "for vm.sh")
        self.assertEqual(self.calls, ["regenerate_project"])

        code, out = self.cli("pins")
        self.assertEqual(code, 0, "a linked app builds, tests and stops as usual: " + out)
        self.assertIn(f"warning: linked to {self.checkout}: `companion.py unlink` before committing", out)
        self.assertIn("(the checkout)           macos/project.yml (linked: not compared)", out)
        self.assertNotIn("must be the same", out)
        code, out = self.cli("pins", "--staged")
        self.assertEqual(code, 1, "a linked app can't be committed")
        self.assertIn(f"linked to {self.checkout}", out)
        self.assertNotIn("warning:", out)
        code, out = self.cli("bump", "0.6.0")
        self.assertEqual(code, 1)
        self.assertIn("unlink", out)
        code, out = self.cli("link")
        self.assertEqual(code, 1)
        self.assertIn("already linked", out)

        # What the linked builds write: SwiftPM drops the remote pin, Xcode records a project Package.resolved.
        (self.root / RESOLVED).write_text('{ "pins" : [ ], "version" : 3 }\n', "utf8")
        (self.root / PROJECT_RESOLVED).parent.mkdir(parents=True)
        (self.root / PROJECT_RESOLVED).write_text('{ "pins" : [ ] }\n', "utf8")
        (self.root / PBXPROJ).write_text("// the linked project\n", "utf8")
        code, out = self.cli("unlink")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status(), "")
        self.assertFalse((self.root / ".git" / "laching-link").exists())
        self.assertEqual(self.calls, ["regenerate_project", "regenerate_project"])
        self.assertEqual(self.cli("pins")[0], 0)
        self.assertIsNone(companion.linked_dotnet(companion.load(self.root)))
        self.assertIn("Not linked", self.cli("unlink")[1])

    def test_unlink_keeps_what_else_changed_meanwhile(self):
        self.cli("link")
        spec = self.root / "macos/project.yml"
        spec.write_text(spec.read_text("utf8") + "# added while linked\n", "utf8")
        code, out = self.cli("unlink")
        self.assertEqual(code, 0, out)
        self.assertIn("the link's edit only", out)
        self.assertEqual(self.status(), "M macos/project.yml")
        self.assertEqual(git(self.root, "diff", "--unified=0", "macos/project.yml").splitlines()[-1],
                         "+# added while linked")

    def test_an_existing_targets_file_gets_the_link_and_loses_it(self):
        targets = self.root / "windows/Directory.Build.targets"
        targets.write_bytes(b"<Project>\r\n  <PropertyGroup />\r\n</Project>\r\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "targets")
        self.cli("link")
        text = targets.read_bytes().decode("utf8")
        self.assertTrue(text.startswith("<Project>\r\n  <PropertyGroup />\r\n  <!-- laching link"))
        self.assertTrue(text.endswith("</ItemGroup>\r\n</Project>\r\n"))
        self.assertEqual(text.count("\n"), text.count("\r\n"), "the file's own line endings")
        self.cli("unlink")
        self.assertEqual(self.status(), "")

    def test_link_takes_another_checkout_and_refuses_what_isnt_one(self):
        code, out = self.cli("link", str(self.root.parent / "laching-origin"))
        self.assertEqual(code, 0, out)
        self.assertIn("path: ../../laching-origin", (self.root / "macos/project.yml").read_text("utf8"))
        self.assertIn("named laching, not laching-origin", out)
        self.cli("unlink")
        code, out = self.cli("link", str(self.root / "macos"))
        self.assertEqual(code, 1)
        self.assertIn("isn't a Laching checkout", out)
        self.assertEqual(self.status(), "")

    def test_link_points_the_android_build_at_the_checkouts_kotlin(self):
        (self.checkout / "kotlin").mkdir()
        (self.checkout / "kotlin" / "settings.gradle.kts").write_text('rootProject.name = "laching"\n', "utf8")
        (self.checkout / "kotlin" / "gradle.properties").write_text("lachingVersion=0.5.0\n", "utf8")
        manifest = self.root / "companion.json"
        data = json.loads(manifest.read_text("utf8"))
        data["android"] = {"dir": "android"}
        manifest.write_text(json.dumps(data), "utf8")
        properties = self.root / "android" / "local.properties"
        properties.parent.mkdir()
        properties.write_text("sdk.dir=/opt/android", "utf8")  # no final newline
        (self.root / ".gitignore").write_text("android/local.properties\n", "utf8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "android")

        code, out = self.cli("link")
        self.assertEqual(code, 0, out)
        self.assertIn("changed  android/local.properties", out)
        text = properties.read_text("utf8")
        self.assertTrue(text.startswith("sdk.dir=/opt/android\n# laching link:"), text)
        self.assertTrue(text.endswith("\nlaching.kotlin.dir=../../laching/kotlin\n"), text)
        code, out = self.cli("unlink")
        self.assertEqual(code, 0, out)
        self.assertEqual(properties.read_text("utf8"), "sdk.dir=/opt/android")
        self.assertEqual(self.status(), "")

        # Without a local.properties the link writes one and unlink removes it; one left behind is still a link.
        properties.unlink()
        self.cli("link")
        text = properties.read_text("utf8")
        self.assertTrue(text.startswith("# laching link:"), text)
        self.cli("unlink")
        self.assertFalse(properties.exists())
        properties.write_text(text, "utf8")
        code, out = self.cli("pins")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: linked to a Laching checkout (local.properties)", out)
        code, out = self.cli("pins", "--staged")
        self.assertEqual(code, 1)
        self.assertIn("linked to a Laching checkout (local.properties)", out)

    def test_a_link_left_in_a_committed_file_fails_the_pins_of_a_commit_and_of_ci(self):
        self.cli("link")
        targets = (self.root / "windows/Directory.Build.targets").read_text("utf8")
        self.cli("unlink")
        (self.root / "windows/Directory.Build.targets").write_text(targets, "utf8")
        code, out = self.cli("pins")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: linked to a Laching checkout (Directory.Build.targets)", out)
        code, out = self.cli("pins", "--staged")
        self.assertEqual(code, 1)
        self.assertIn("linked to a Laching checkout (Directory.Build.targets)", out)
        for runner in ({"GITHUB_ACTIONS": "true"}, {"CI": "true"}, {"CI": "1"}):
            with mock.patch.dict(os.environ, runner):
                code, out = self.cli("pins")
            self.assertEqual(code, 1, f"a runner got the link: {runner}")
            self.assertIn("linked to a Laching checkout (Directory.Build.targets)", out)
        self.assertEqual(self.cli("pins", "--fast")[0], 2, "only --staged is an argument")

    def test_the_pins_that_are_left_are_still_compared_while_linked(self):
        self.cli("link")
        workflow = self.root / ".github/workflows/macos.yml"
        workflow.write_text(workflow.read_text("utf8").replace("@v0.5.0", "@v0.4.0"), "utf8")
        code, out = self.cli("pins")
        self.assertEqual(code, 1, out)
        self.assertIn("must be the same", out)
        self.assertIn("v0.4.0", out)
        self.assertIn(f"warning: linked to {self.checkout}", out)

    @unittest.skipIf(BASH is None, NO_SHELL)
    def test_a_linked_app_passes_verify_until_a_commit_or_ci(self):
        """The three cases of a link: the fast pass (what the Stop hook runs) warns, the pre-commit hook's fast pass
        and the reusable workflows' `pins` stage fail."""
        self.cli("link")

        def run(command: list[str], **env: str) -> subprocess.CompletedProcess:
            return subprocess.run(command, cwd=self.root, capture_output=True, encoding="utf8", env={
                **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, **env})

        verify = shell(TOOLS / "verify.sh")
        fast = run([*verify, "--fast", "--", "notes.md"])
        self.assertEqual(fast.returncode, 0, fast.stdout + fast.stderr)
        self.assertEqual(fast.stdout.splitlines()[:2], [
            "ok    Laching pins agree", f"warn  linked to {self.checkout}: `companion.py unlink` before committing"])

        staged = run([*verify, "--fast", "--staged"])
        self.assertEqual(staged.returncode, 1, staged.stdout + staged.stderr)
        self.assertIn("FAIL  Laching pins agree", staged.stdout)
        self.assertIn(f"linked to {self.checkout}", staged.stdout)
        self.assertNotIn("warn  ", staged.stdout)

        stage = [command for _, command in companion_ci.pins(companion.load(self.root), TOOLS) if "pins" in command]
        self.assertEqual(len(stage), 1, stage)
        on_runner = run(stage[0], GITHUB_ACTIONS="true", CI="true")
        self.assertEqual(on_runner.returncode, 1, on_runner.stdout + on_runner.stderr)
        self.assertIn(f"linked to {self.checkout}", on_runner.stdout)
        self.assertEqual(run([*verify, "--fast", "--", "notes.md"], CI="true").returncode, 1, "verify.sh on a runner")

        self.cli("unlink")
        fast = run([*verify, "--fast", "--", "notes.md"])
        self.assertEqual(fast.returncode, 0, fast.stdout + fast.stderr)
        self.assertNotIn("warn  ", fast.stdout)


if __name__ == "__main__":
    unittest.main()
