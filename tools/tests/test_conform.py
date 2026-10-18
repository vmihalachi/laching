"""tools/conform.py over the fixture app: `companion.py conform` (wrappers, not copies, of Laching's tools; the reusable
Windows workflows at the pinned release; skills linked into laching/tools/skills; store.microsoft as the schema wants
it) and `companion.py apps` (the apps' pins against the releases)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from support import TOOLS, FixtureAppTestCase, git, quiet

sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402
import conform  # noqa: E402

WRAPPER = """# The app's smoke-package.ps1: Laching's, which says how.
& "$PSScriptRoot/../../laching/tools/windows/smoke-package.ps1" @args
exit $LASTEXITCODE
"""


class ConformTests(FixtureAppTestCase):
    def problems(self) -> list[str]:
        return conform.problems(companion.load(self.root, check=False))

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf8")
        return path

    def edit_config(self, change) -> None:
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        change(data)
        path.write_text(json.dumps(data), "utf8")

    def test_the_fixture_conforms(self):
        self.assertEqual(self.problems(), [])
        done, out = quiet(conform.check, companion.load(self.root))
        self.assertEqual((done, out.strip()), (0, "conform: ok"))

    def test_a_thin_wrapper_is_fine_and_a_copy_is_not(self):
        self.write("windows/scripts/smoke-package.ps1", WRAPPER)
        self.write("store-listing/microsoft/import.py", (
            "#!/usr/bin/env python3\n# Laching's store/import.py for this app.\n"
            "import os, subprocess, sys; root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))\n"
            "sys.exit(subprocess.call([sys.executable, os.path.join(root, 'laching', 'tools', 'store', 'import.py')]))\n"))
        self.write("windows/scripts/screenshots.ps1", "<#\nA long comment\nblock of many lines\n#>\n"
                   "& (Join-Path $PSScriptRoot '..\\..\\laching\\tools\\windows\\screenshots.ps1') @args\n")
        self.assertEqual(self.problems(), [])

        self.write("windows/scripts/check-winui.py", "import re\nprint('my own rules')\n")
        self.write("store-listing/microsoft/push.py", "API = 'https://manage.devcenter.microsoft.com'\n")
        self.write("windows/scripts/store-packages.ps1", "dotnet publish x\n")
        self.write("scripts/verify.sh", "\n".join(["#!/bin/sh", 'exec "$(dirname "$0")/../laching/tools/verify.sh" "$@"']
                                                   + [f"echo extra {i}" for i in range(13)]) + "\n")
        found = self.problems()
        self.assertEqual(len(found), 4, found)
        self.assertTrue(any("windows/scripts/check-winui.py: a copy of laching/tools/windows/check-winui.py" in p for p in found))
        self.assertTrue(any("store-listing/microsoft/push.py: a copy of laching/tools/store/push-microsoft.py" in p for p in found))
        self.assertTrue(any("store-packages.ps1: a copy of laching/tools/windows/store-release.ps1" in p for p in found))
        self.assertTrue(any("scripts/verify.sh: 14 lines of code around laching/tools/verify.sh" in p for p in found))

        # The app's own on purpose: listed with why.
        self.edit_config(lambda data: data.update(conform={"allow": [{"path": "windows/scripts/check-winui.py",
                                                                      "why": "rules of the app's own"}]}))
        self.assertEqual(len(self.problems()), 3)

    def test_files_of_other_names_and_kinds_are_the_apps(self):
        self.write("windows/scripts/run-dev.ps1", "dotnet run\n")
        self.write("docs/verify.md", "How we verify\n")
        self.assertEqual(self.problems(), [])

    def test_workflows_call_the_reusable_ones_at_the_pin(self):
        workflows = self.root / ".github" / "workflows"
        release = workflows / "windows-release.yml"
        release.write_text(release.read_text("utf8").replace("@v0.5.0", "@v0.4.0"), "utf8")
        self.write(".github/workflows/package.yml", "jobs:\n  a:\n    steps:\n      - run: >\n"
                   "          dotnet publish x -p:GenerateAppxPackageOnBuild=true\n")
        self.write(".github/workflows/notes.yml", "# makeappx bundles them, in windows-release.yml\njobs: {}\n")
        found = self.problems()
        self.assertEqual(len(found), 2, found)
        self.assertIn(".github/workflows/package.yml: builds the Store packages itself (GenerateAppxPackageOnBuild)", found[0])
        self.assertIn("calls windows-release.yml@v0.4.0, but the app pins Laching v0.5.0: call it @v0.5.0", found[1])

        (workflows / "windows.yml").unlink()
        self.assertTrue(any("no workflow calls Laching's windows-app.yml" in p for p in self.problems()))
        self.edit_config(lambda data: data["windows"].pop("app"))
        self.assertFalse(any("no workflow calls" in p for p in self.problems()), "only an app with windows.app")

    def test_skills_link_into_laching(self):
        git(self.root, "init", "-q")
        skills = self.root / ".agents" / "skills"
        # A copy of Laching's skill, the app's own skill, and links recorded the way git records them (mode 120000).
        self.write(".agents/skills/release-windows/SKILL.md", "# Release the Windows app, our way\n")
        self.write(".agents/skills/add-exercise/SKILL.md", "# The app's own\n")
        for name, target in (("localization", "../../laching/tools/skills/localization"),
                             ("voice", "../../elsewhere/voice")):
            self.write(f".agents/skills/{name}", target)
            blob = subprocess.run(["git", "-C", str(self.root), "hash-object", "-w", "--stdin"], input=target,
                                  capture_output=True, text=True, check=True).stdout.strip()
            git(self.root, "update-index", "--add", "--cacheinfo", f"120000,{blob},.agents/skills/{name}")
        git(self.root, "add", ".agents/skills/release-windows", ".agents/skills/add-exercise")
        found = [p for p in self.problems() if p.startswith(".agents")]
        self.assertEqual(len(found), 2, found)
        self.assertIn(".agents/skills/release-windows: a copy of laching/tools/skills/release-windows: link it", found[0])
        self.assertIn(".agents/notes/release-windows.md", found[0])
        self.assertIn(".agents/skills/voice: links to ../../elsewhere/voice", found[1])
        self.assertTrue(skills.is_dir())

    def test_store_microsoft_as_the_schema_wants_it(self):
        self.edit_config(lambda data: data["store"].update(microsoft={"screenshotCount": 8, "publisher": "Fixture Studio"}))
        found = self.problems()
        self.assertIn("companion.json: store.microsoft: missing shots", found)
        self.assertIn("companion.json: store.microsoft: unknown key screenshotCount", found)
        # companion.py conform reports it with the rest, where every other command refuses the file.
        done = subprocess.run([sys.executable, str(TOOLS / "companion.py"), "conform"], cwd=self.root, capture_output=True,
                              text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("conform: companion.json: store.microsoft: missing shots", done.stdout)

    def test_code_lines(self):
        self.assertEqual(conform.code_lines('"""Doc\nmore\n"""\nimport x\n# c\n\nx.run()\n', ".py"), ["import x", "x.run()"])
        self.assertEqual(conform.code_lines("<# a\nb #>\n& x @args\n", ".ps1"), ["& x @args"])
        self.assertEqual(conform.code_lines("<# one line #>\nexit 0\n", ".ps1"), ["exit 0"])


class AppsTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.laching = Path(temp.name) / "laching"
        self.laching.mkdir()
        git(self.laching, "init", "-q")
        for version in ("0.4.0", "0.5.0", "0.6.0"):
            git(self.laching, "commit", "-q", "--allow-empty", "-m", version)
            git(self.laching, "tag", f"v{version}")
        git(self.laching, "commit", "-q", "--allow-empty", "-m", "since")
        self.other = Path(temp.name) / "other"
        shutil.copytree(self.root, self.other)

    def pin(self, root: Path, tag: str) -> None:
        path = root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["laching"]["tag"] = tag
        path.write_text(json.dumps(data), "utf8")

    def test_reports_each_apps_pin_against_the_releases(self):
        self.pin(self.root, "v0.6.0")
        self.pin(self.other, "v0.5.0")
        done, out = quiet(conform.apps, [self.root, self.other], self.laching)
        self.assertEqual(done, 1)
        self.assertIn("Laching: newest release v0.6.0, this checkout 1 commit(s) past it", out)
        self.assertIn(f"{self.root.name}: v0.6.0 (newest,", out)
        self.assertIn("other: v0.5.0 (1 release(s) behind,", out)
        self.assertIn("The apps pin different releases", out)

    def test_apps_on_the_newest_release_and_in_step(self):
        git(self.laching, "tag", "-d", "v0.6.0")
        done, out = quiet(conform.apps, [self.root, self.other], self.laching)
        self.assertIn(f"{self.root.name}: v0.5.0 (newest, conforms)", out)
        self.assertIn("other: v0.5.0 (newest, conforms)", out)
        self.assertNotIn("different releases", out)
        self.assertEqual(done, 0, out)
