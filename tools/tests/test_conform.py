"""tools/conform.py over the fixture app: `companion.py conform` (wrappers, not copies, of Laching's tools; the reusable
Windows workflows at the pinned release; no dispatch-only workflow that cancels its own runs; Laching's hooks in
.claude/settings.json, called through the app's wrapper; skills linked into laching/tools/skills; store.microsoft as the
schema wants it) and `companion.py apps` (the apps' pins against the releases, and a link to a checkout)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from support import TOOLS, FixtureAppTestCase, git, quiet

sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402
import conform  # noqa: E402

WRAPPER = """# The app's smoke-package.ps1: Laching's, which says how.
$previous = $env:COMPANION_ROOT
$env:COMPANION_ROOT = (Resolve-Path "$PSScriptRoot/../..").Path
try { & "$PSScriptRoot/../../laching/tools/windows/smoke-package.ps1" @args } finally { $env:COMPANION_ROOT = $previous }
exit $LASTEXITCODE
"""
TEMPLATES = "see the templates in laching/tools/README.md, *Staying on Laching's tools*"
# An app's .claude/settings.json: Laching's hooks, and the permissions that are the app's own.
SETTINGS = """{
  "permissions": { "allow": ["Bash(python3 laching/tools/hooks/hook.py:*)"], "deny": ["Read(./voices/**)"] },
  "hooks": {
    "SessionStart": [
      { "matcher": "startup", "hooks": [{ "type": "command", "timeout": 600,
          "command": "[ \\"$CLAUDE_CODE_REMOTE\\" != true ] || \\"$CLAUDE_PROJECT_DIR/scripts/setup-linux.sh\\"" }] },
      { "matcher": "compact|resume", "hooks": [{ "type": "command", "timeout": 10,
          "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py\\" resume" }] }
    ],
    "PreToolUse": [{ "matcher": "Read|Edit|MultiEdit|Write|NotebookEdit|Grep|Bash", "hooks": [
      { "type": "command", "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py\\" pre", "timeout": 10 }] }],
    "PostToolUse": [{ "matcher": "Edit|MultiEdit|Write|NotebookEdit|Bash", "hooks": [
      { "type": "command", "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py\\" post", "timeout": 60 }] }],
    "Stop": [{ "hooks": [
      { "type": "command", "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py\\" stop", "timeout": 180 }] }]
  }
}
"""
HOOK_WRAPPER = """#!/usr/bin/env python3
# Laching's agent hook for this app, which every agent's hooks call.
import os, subprocess, sys
root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["COMPANION_ROOT"] = root
sys.exit(subprocess.call([sys.executable, os.path.join(root, "laching", "tools", "hooks", "hook.py"), *sys.argv[1:]]))
"""
SETUP_LINUX = """#!/bin/sh
export COMPANION_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$COMPANION_ROOT/laching/tools/setup-linux.sh" ] || git -C "$COMPANION_ROOT" submodule update --init laching
exec "$COMPANION_ROOT/laching/tools/setup-linux.sh" "$@"
"""
DISPATCHED = """name: Extra
on:
  workflow_dispatch:
    inputs:
      hosted:
        type: boolean
permissions:
  contents: read
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  extra:
    runs-on: ubuntu-latest
    steps:
      - run: "true"
"""


class ConformTests(FixtureAppTestCase):
    def problems(self) -> list[str]:
        return conform.problems(companion.load(self.root, check=False))

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf8")
        return path

    def link(self, name: str, target: str) -> None:
        """A link recorded the way git records one (mode 120000), with the text file a checkout without symlinks has."""
        self.write(name, target)
        blob = subprocess.run(["git", "-C", str(self.root), "hash-object", "-w", "--stdin"], input=target,
                              capture_output=True, encoding="utf8", check=True).stdout.strip()
        git(self.root, "update-index", "--add", "--cacheinfo", f"120000,{blob},{name}")

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
            "os.environ['COMPANION_ROOT'] = root\n"
            "sys.exit(subprocess.call([sys.executable, os.path.join(root, 'laching', 'tools', 'store', 'import.py'),\n"
            "                          *sys.argv[1:]]))\n"))
        self.write("windows/scripts/screenshots.ps1", "<#\nA long comment\nblock of many lines\n#>\n"
                   "$env:COMPANION_ROOT = (Resolve-Path \"$PSScriptRoot\\..\\..\").Path\n"
                   "& (Join-Path $PSScriptRoot '..\\..\\laching\\tools\\windows\\screenshots.ps1') @args\n"
                   "exit $LASTEXITCODE\n")
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

    def test_a_wrapper_sets_the_root_forwards_its_arguments_and_keeps_the_status(self):
        # The forms the apps use, each with all three.
        self.write("macos/scripts/check-layout.sh", "#!/bin/sh\nCOMPANION_ROOT=$(cd \"$(dirname \"$0\")/../..\" && pwd) "
                   "exec \"$(dirname \"$0\")/../../laching/tools/check-layout.sh\" \"$@\"\n")
        self.write("macos/scripts/conformance.sh", "#!/bin/sh\nexport COMPANION_ROOT=\"$(cd \"$(dirname \"$0\")/../..\" && pwd)\"\n"
                   "exec \"$COMPANION_ROOT/laching/tools/conformance.sh\" \"$@\"\n")
        self.write("macos/scripts/voice.py", "import os, sys; root = os.path.abspath(os.path.join(os.path.dirname(__file__), "
                   "'..', '..')); os.environ['COMPANION_ROOT'] = root\nos.execv(sys.executable, [sys.executable, "
                   "os.path.join(root, 'laching', 'tools', 'voice.py'), *sys.argv[1:]])\n")
        self.write("macos/scripts/character.py", "import os, pathlib, runpy\nroot = pathlib.Path(__file__).resolve()"
                   ".parents[2]\nos.environ['COMPANION_ROOT'] = str(root)\nrunpy.run_path(str(root / "
                   "'laching/tools/character.py'), run_name='__main__')\n")
        self.write("macos/scripts/conformance.py", "import os, subprocess, sys\nos.environ['COMPANION_ROOT'] = "
                   "'..'\nsubprocess.call([sys.executable, '../laching/tools/conformance.sh'])\n")
        self.write("windows/scripts/smoke-package.ps1", WRAPPER)
        self.assertEqual(self.problems(), [
            "macos/scripts/conformance.py: a wrapper of laching/tools/conformance.sh that doesn't forward its arguments "
            "(sys.argv[1:], or runpy.run_path(..., run_name=\"__main__\")) or keep the exit status (os.execv, "
            "sys.exit(subprocess.call(...)) or runpy.run_path(..., run_name=\"__main__\")): " + TEMPLATES])
        (self.root / "macos/scripts/conformance.py").unlink()

        self.write("macos/scripts/install-dev.sh", "#!/bin/sh\nexec \"$(dirname \"$0\")/../../laching/tools/install-dev.sh\" \"$@\"\n")
        self.write("macos/scripts/l10n.py", "import pathlib, runpy; runpy.run_path(str(pathlib.Path(__file__).resolve()"
                   ".parents[2] / 'laching/tools/l10n.py'), run_name='__main__')\n")
        self.write("windows/scripts/screenshots.ps1", "$ErrorActionPreference = 'Stop'\n"
                   "& (Join-Path $PSScriptRoot '..\\..\\laching\\tools\\windows\\screenshots.ps1') @args\n"
                   "if ($LASTEXITCODE) { throw \"screenshots.ps1 failed\" }\n")
        self.write("scripts/verify.sh", "#!/bin/sh\nexport COMPANION_ROOT=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"\n"
                   "\"$COMPANION_ROOT/laching/tools/verify.sh\" \"$@\" || true\n")
        self.write("macos/scripts/phrases.py", "import os, sys; root = os.path.abspath(os.path.join(os.path.dirname(__file__), "
                   "'..', '..')); os.environ['COMPANION_ROOT'] = root\nos.execv(sys.executable, [sys.executable, "
                   "os.path.join(root, 'laching', 'tools', 'l10n.py'), *sys.argv[1:]])\n")
        found = self.problems()
        self.assertEqual(found, [
            "macos/scripts/install-dev.sh: a wrapper of laching/tools/install-dev.sh that doesn't export COMPANION_ROOT: "
            + TEMPLATES,
            "macos/scripts/l10n.py: a wrapper of laching/tools/l10n.py that doesn't set os.environ[\"COMPANION_ROOT\"]: "
            + TEMPLATES,
            "macos/scripts/phrases.py: a wrapper of laching/tools/phrases.py that doesn't call laching/tools/phrases.py: "
            "see the templates in laching/tools/README.md, *Staying on Laching's tools*",
            "scripts/verify.sh: a wrapper of laching/tools/verify.sh that doesn't keep the exit status (exec): see the "
            "templates in laching/tools/README.md, *Staying on Laching's tools*",
            "windows/scripts/screenshots.ps1: a wrapper of laching/tools/windows/screenshots.ps1 that doesn't set "
            "$env:COMPANION_ROOT or keep the exit status (exit $LASTEXITCODE): see the templates in "
            "laching/tools/README.md, *Staying on Laching's tools*"], found)

        self.edit_config(lambda data: data.update(conform={"allow": [{"path": "scripts/verify.sh", "why": "ours"}]}))
        self.assertEqual(len(self.problems()), 4)

    def test_a_wrapper_hands_the_root_to_the_tool_and_runs_it(self):
        # An unexported variable, or one set before a separate exec, never reaches the tool.
        self.write("macos/scripts/install-dev.sh", "#!/bin/sh\nCOMPANION_ROOT=\"$(cd \"$(dirname \"$0\")/../..\" && pwd)\"\n"
                   "exec \"$COMPANION_ROOT/laching/tools/install-dev.sh\" \"$@\"\n")
        self.write("macos/scripts/check-layout.sh", "#!/bin/sh\nCOMPANION_ROOT=.. ; exec \"$(dirname \"$0\")/../../laching/"
                   "tools/check-layout.sh\" \"$@\"\n")
        # Reading the variable isn't setting it; without run_name the tool's __main__ block never runs.
        self.write("macos/scripts/l10n.py", "import os, pathlib, runpy\nprint(os.environ.get('COMPANION_ROOT'))\n"
                   "runpy.run_path(str(pathlib.Path(__file__).resolve().parents[2] / 'laching/tools/l10n.py'), "
                   "run_name='__main__')\n")
        self.write("macos/scripts/voice.py", "import os, pathlib, runpy\nroot = pathlib.Path(__file__).resolve().parents[2]\n"
                   "os.environ['COMPANION_ROOT'] = str(root)\nrunpy.run_path(str(root / 'laching/tools/voice.py'))\n")
        # PowerShell's names in any case.
        self.write("windows/scripts/smoke-package.ps1", "$Env:Companion_Root = (Resolve-Path \"$PSScriptRoot/../..\").Path\n"
                   "& \"$PSScriptRoot/../../laching/tools/windows/smoke-package.ps1\" @Args\nEXIT $LastExitCode\n")
        # The same stem, the wrong tool: screenshots.ps1 wraps windows/screenshots.ps1, not screenshots.sh.
        self.write("windows/scripts/screenshots.ps1", "$env:COMPANION_ROOT = (Resolve-Path \"$PSScriptRoot/../..\").Path\n"
                   "& \"$PSScriptRoot/../../laching/tools/screenshots.sh\" @args\nexit $LASTEXITCODE\n")
        self.assertEqual(self.problems(), [
            "macos/scripts/check-layout.sh: a wrapper of laching/tools/check-layout.sh that doesn't export COMPANION_ROOT: "
            + TEMPLATES,
            "macos/scripts/install-dev.sh: a wrapper of laching/tools/install-dev.sh that doesn't export COMPANION_ROOT: "
            + TEMPLATES,
            "macos/scripts/l10n.py: a wrapper of laching/tools/l10n.py that doesn't set os.environ[\"COMPANION_ROOT\"]: "
            + TEMPLATES,
            "macos/scripts/voice.py: a wrapper of laching/tools/voice.py that doesn't forward its arguments (sys.argv[1:], "
            "or runpy.run_path(..., run_name=\"__main__\")) or keep the exit status (os.execv, sys.exit(subprocess.call("
            "...)) or runpy.run_path(..., run_name=\"__main__\")): " + TEMPLATES,
            "windows/scripts/screenshots.ps1: a wrapper of laching/tools/windows/screenshots.ps1 that doesn't call "
            "laching/tools/windows/screenshots.ps1: " + TEMPLATES])

    def test_setup_linux_checks_out_the_submodule_before_running_lachings(self):
        head = "#!/bin/sh\nexport COMPANION_ROOT=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"\n"
        run = "exec \"$COMPANION_ROOT/laching/tools/setup-linux.sh\" \"$@\"\n"
        self.write("scripts/setup-linux.sh", head + run)
        self.assertEqual(self.problems(), [
            "scripts/setup-linux.sh: runs laching/tools/setup-linux.sh before checking out laching/, which a clone made "
            "without its submodules lacks: check it out first ([ -f ... ] || git -C \"$COMPANION_ROOT\" submodule update "
            "--init laching)"])
        self.write("scripts/setup-linux.sh", head + run + "git submodule update --init laching\n")
        self.assertEqual(len(self.problems()), 1, "too late")

        self.write("scripts/setup-linux.sh", head + "[ -f \"$COMPANION_ROOT/laching/tools/setup-linux.sh\" ] || "
                   "git -C \"$COMPANION_ROOT\" submodule update --init laching\n" + run)
        self.assertEqual(self.problems(), [])
        self.write("scripts/setup-linux.sh", head + "git -C \"$COMPANION_ROOT\" submodule update --init --quiet laching\n" + run)
        self.assertEqual(self.problems(), [])

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
        self.assertTrue(any(".github/workflows/package.yml: builds the Store packages itself (GenerateAppxPackageOnBuild)"
                            in p for p in found), found)
        self.assertTrue(any("calls windows-release.yml@v0.4.0, but the app pins Laching v0.5.0: call it @v0.5.0" in p
                            for p in found), found)

        (workflows / "windows.yml").unlink()
        self.assertTrue(any("no workflow calls Laching's windows-app.yml" in p for p in self.problems()))
        self.edit_config(lambda data: data["windows"].pop("app"))
        self.assertFalse(any("no workflow calls" in p for p in self.problems()), "only an app with windows.app")

    def test_macos_workflow_refs_are_checked_against_the_same_release(self):
        self.write(".github/workflows/macos.yml", "jobs:\n  test:\n"
                   "    uses: vmihalachi/laching/.github/workflows/macos-app.yml@v0.4.0\n")
        self.assertIn(".github/workflows/macos.yml: calls macos-app.yml@v0.4.0, but the app pins Laching v0.5.0: "
                      "call it @v0.5.0", self.problems())
        # A branch ref, read whole.
        self.write(".github/workflows/macos.yml", "jobs:\n  test:\n"
                   "    uses: vmihalachi/laching/.github/workflows/macos-app.yml@users/vlad/macos-ci\n")
        self.assertIn(".github/workflows/macos.yml: calls macos-app.yml@users/vlad/macos-ci, but the app pins Laching "
                      "v0.5.0: call it @v0.5.0", self.problems())
        self.write(".github/workflows/macos.yml", "jobs:\n  test:\n"
                   "    uses: vmihalachi/laching/.github/workflows/macos-app.yml@v0.5.0\n")
        self.assertEqual(self.problems(), [])

    def test_an_app_with_mac_calls_macos_app(self):
        self.write(".github/workflows/macos.yml", "# Once called macos-app.yml; now runs its own steps\n"
                   "jobs:\n  test:\n    steps:\n      - run: xcodebuild test\n")
        self.assertEqual(self.problems(),
                         [".github/workflows: no workflow calls Laching's macos-app.yml (the app has mac)"])
        self.edit_config(lambda data: data.pop("mac"))
        self.assertEqual(self.problems(), [], "only an app with mac")

    def test_windows_core_workflow_ref_is_checked_against_the_same_release(self):
        self.write(".github/workflows/windows-core.yml", "jobs:\n  test:\n"
                   "    uses: vmihalachi/laching/.github/workflows/windows-core.yml@v0.4.0\n")
        self.assertIn(".github/workflows/windows-core.yml: calls windows-core.yml@v0.4.0, but the app pins Laching v0.5.0: "
                      "call it @v0.5.0", self.problems())
        self.write(".github/workflows/windows-core.yml", "jobs:\n  test:\n"
                   "    uses: vmihalachi/laching/.github/workflows/windows-core.yml@v0.5.0\n")
        self.assertEqual(self.problems(), [])

    def test_shared_linux_workflows_are_called_not_copied(self):
        self.write(".github/workflows/core.yml", "jobs:\n  test:\n    container: swift:6.1\n    steps:\n"
                   "      - run: swift test --package-path macos/FixtureCore\n")
        self.write(".github/workflows/regenerate-vectors.yml", "# uses: vmihalachi/laching/.github/workflows/"
                   "vectors-linux.yml@v0.5.0\njobs:\n  a:\n    steps:\n      - run: sh macos/scripts/conformance.sh\n")
        self.write(".github/workflows/copilot-setup-steps.yml", "# scripts/setup-linux.sh does it in the cloud\n"
                   "jobs:\n  copilot-setup-steps:\n    steps:\n      - uses: actions/setup-dotnet@v5\n")
        found = self.problems()
        self.assertEqual(len(found), 3, found)
        self.assertIn(".github/workflows/core.yml: a copy of Laching's core-linux.yml: call it instead "
                      "(uses: vmihalachi/laching/.github/workflows/core-linux.yml@v0.5.0)", found)
        self.assertTrue(any(p.startswith(".github/workflows/regenerate-vectors.yml: a copy of Laching's vectors-linux.yml")
                            for p in found), "a comment isn't a call")
        self.assertTrue(any(p.startswith(".github/workflows/copilot-setup-steps.yml: sets up its own tools") for p in found))

        self.write(".github/workflows/core.yml", "jobs:\n  test:\n"
                   "    uses: vmihalachi/laching/.github/workflows/core-linux.yml@v0.5.0\n")
        (self.root / ".github/workflows/regenerate-vectors.yml").unlink()
        self.write(".github/workflows/regenerate-vectors.yaml", "jobs:\n  regenerate:\n"
                   "    uses: vmihalachi/laching/.github/workflows/vectors-linux.yml@v0.5.0\n")
        self.write(".github/workflows/copilot-setup-steps.yml", "jobs:\n  copilot-setup-steps:\n    steps:\n"
                   "      - uses: actions/checkout@v7\n      - run: scripts/setup-linux.sh\n")
        self.assertEqual(self.problems(), [])
        self.write(".github/workflows/copilot-setup-steps.yml", "jobs:\n  copilot-setup-steps:\n    steps:\n"
                   "      - name: Set up\n        run: |\n          git submodule update --init laching\n"
                   "          bash scripts/setup-linux.sh\n")
        self.assertEqual(self.problems(), [])
        # Named after it, or in a comment, isn't running it.
        self.write(".github/workflows/copilot-setup-steps.yml", "jobs:\n  copilot-setup-steps:\n    steps:\n"
                   "      - name: Run scripts/setup-linux.sh\n        run: ./install.sh  # not scripts/setup-linux.sh\n")
        self.assertEqual(len(self.problems()), 1)
        self.write(".github/workflows/copilot-setup-steps.yml", "jobs:\n  copilot-setup-steps:\n    steps:\n"
                   "      - uses: actions/checkout@v7\n      - run: scripts/setup-linux.sh\n")

        # Kept on purpose, with why; and other names are the app's own.
        self.write(".github/workflows/core.yml", "jobs: {}\n")
        self.write(".github/workflows/core-extra.yml", "jobs: {}\n")
        self.assertEqual(len(self.problems()), 1)
        self.edit_config(lambda data: data.update(conform={"allow": [{"path": ".github/workflows/core.yml",
                                                                      "why": "a core of the app's own"}]}))
        self.assertEqual(self.problems(), [])

    def test_a_workflow_only_a_dispatch_starts_does_not_cancel_its_runs(self):
        said = (".github/workflows/extra.yml: runs only on workflow_dispatch and sets cancel-in-progress: true: no push "
                "supersedes a run there, so a second dispatch on the same branch would only cancel a run that is nearly "
                "done: set it to false")
        block = "on:\n  workflow_dispatch:\n    inputs:\n      hosted:\n        type: boolean\n"
        only_dispatched = (block, "on: workflow_dispatch\n", "on: [workflow_dispatch]  # by hand\n", "'on':\n- workflow_dispatch\n",
                           '"on":\n    workflow_dispatch:\n')
        for on in only_dispatched:
            self.write(".github/workflows/extra.yml", DISPATCHED.replace(block, on))
            self.assertEqual(self.problems(), [said], on)
        self.write(".github/workflows/extra.yml", DISPATCHED.replace(
            "concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n  cancel-in-progress: true\n",
            "concurrency: { group: extra, cancel-in-progress: True }\n"))
        self.assertEqual(self.problems(), [said], "a flow mapping, and YAML's True")

        # A push supersedes a run, so there the setting earns its place.
        pushed = (block + "  push:\n    branches: [main]\n", "on: [push, workflow_dispatch]\n", "on:\n  - workflow_dispatch\n  - pull_request\n",
                  "on: push\n", "on: workflow_call\n")
        for on in pushed:
            self.write(".github/workflows/extra.yml", DISPATCHED.replace(block, on))
            self.assertEqual(self.problems(), [], on)
        # Not cancelling, an expression that decides per run, a comment, and a trigger written a way this doesn't read.
        for old, new in (("cancel-in-progress: true", "cancel-in-progress: false"),
                         ("cancel-in-progress: true", "cancel-in-progress: ${{ !inputs.hosted }}"),
                         ("  cancel-in-progress: true", "  # cancel-in-progress: true"),
                         ("  cancel-in-progress: true\n", ""),
                         (block, "on: { workflow_dispatch: {} }\n"), (block, "on: [workflow_dispatch,\n  push]\n")):
            self.write(".github/workflows/extra.yml", DISPATCHED.replace(old, new))
            self.assertEqual(self.problems(), [], new)

        self.write(".github/workflows/extra.yml", DISPATCHED)
        self.edit_config(lambda data: data.update(conform={"allow": [{"path": ".github/workflows/extra.yml",
                                                                      "why": "one run at a time, the newest"}]}))
        self.assertEqual(self.problems(), [])

    def test_triggers(self):
        self.assertEqual(conform.triggers("name: x\non:\n  push:\n    branches: [main]\n  workflow_dispatch:\njobs: {}\n"),
                         {"push", "workflow_dispatch"})
        self.assertEqual(conform.triggers("on: [ push, 'pull_request' ]\n"), {"push", "pull_request"})
        self.assertEqual(conform.triggers('on: "workflow_dispatch"  # by hand\n'), {"workflow_dispatch"})
        self.assertIsNone(conform.triggers("jobs:\n  a:\n    with:\n      on: workflow_dispatch\n"), "not the workflow's own")
        self.assertIsNone(conform.triggers("on:\n"))

    def settings(self, change=None, text: str = SETTINGS) -> None:
        """The app's .claude/settings.json, after `change` to its hooks block."""
        data = json.loads(text)
        if change:
            change(data["hooks"])
        self.write(".claude/settings.json", json.dumps(data, indent=2) + "\n")

    def test_the_hooks_block_is_lachings(self):
        self.write(".claude/hooks/hook.py", HOOK_WRAPPER)
        self.write("scripts/setup-linux.sh", SETUP_LINUX)
        self.settings()
        self.assertEqual(self.problems(), [], "Laching's hooks, and permissions of the app's own")
        # The block the README shows is the one conform asks for.
        shown = re.search(r'```json\n( *"hooks": \{.*?)\n *```', (TOOLS / "README.md").read_text("utf-8"), re.S)
        self.write(".claude/settings.json", "{" + shown.group(1) + "}\n")
        self.assertEqual(len(conform.hook_calls(json.loads("{" + shown.group(1) + "}"))), len(conform.HOOKS) + 1)
        self.assertEqual(self.problems(), [])
        self.settings()

        # Hooks, events and keys of the app's own are the app's.
        def own(hooks):
            hooks["PreToolUse"].insert(0, {"matcher": "Bash", "hooks": [{"type": "command", "command": "scripts/own.sh"}]})
            hooks["PreToolUse"][1]["hooks"].append({"type": "command", "command": "scripts/also.sh", "timeout": 5})
            hooks["Stop"][0]["hooks"][0]["statusMessage"] = "Checking"
            hooks["Notification"] = [{"hooks": [{"type": "command", "command": "scripts/notify.sh"}]}]
        self.settings(own)
        self.assertEqual(self.problems(), [])

        pre = (".claude/settings.json: hooks.PreToolUse lacks Laching's hook (the matcher, the command and the timeout as "
               'here): {"matcher": "Read|Edit|MultiEdit|Write|NotebookEdit|Grep|Bash", "hooks": [{"type": "command", '
               '"command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py\\" pre", "timeout": 10}]}')
        self.settings(lambda hooks: hooks.pop("PreToolUse"))
        self.assertEqual(self.problems(), [pre])
        self.assertEqual(json.loads(pre.split("here): ")[1])["hooks"][0]["timeout"], 10, "the line holds what to paste")
        for change in (lambda hooks: hooks["PreToolUse"][0].update(matcher="Edit|Write"),
                       lambda hooks: hooks["PreToolUse"][0]["hooks"][0].update(timeout=30),
                       lambda hooks: hooks["PreToolUse"][0]["hooks"][0].update(type="prompt"),
                       lambda hooks: hooks["PreToolUse"][0]["hooks"][0].update(command="python3 .claude/hooks/hook.py pre"),
                       lambda hooks: hooks.update(PreToolUse={"matcher": "Bash"}),
                       lambda hooks: hooks.update(PreToolUse=["hook.py", {"matcher": ["Bash"], "hooks": "hook.py"}])):
            self.settings(change)
            self.assertEqual(self.problems(), [pre])

        def other_events(hooks):
            hooks["Stop"][0]["matcher"] = "*"
            hooks["SessionStart"] = hooks["SessionStart"][1:]
            hooks["PostToolUse"][0]["hooks"][0]["timeout"] = 600
        self.settings(other_events)
        found = self.problems()
        self.assertEqual(len(found), 3, found)
        self.assertEqual([p.split(" lacks ")[0] for p in found], [f".claude/settings.json: hooks.{event}" for event in (
            "PostToolUse", "Stop", "SessionStart")])
        self.assertIn('{"matcher": "startup", "hooks": [{"type": "command", "command": "[ \\"$CLAUDE_CODE_REMOTE\\" != true ] '
                      '|| \\"$CLAUDE_PROJECT_DIR/scripts/setup-linux.sh\\"", "timeout": 600}]}', found[2])
        self.assertIn('hooks.Stop lacks Laching\'s hook (the matcher, the command and the timeout as here): {"hooks": [{"type": '
                      '"command", "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py\\" stop", "timeout": 180}]}',
                      found[1], "Stop has no matcher")
        # The cloud session's setup is asked of an app that has one.
        (self.root / "scripts/setup-linux.sh").unlink()
        self.assertEqual(len(self.problems()), 2)

        # A settings.json without hooks lacks them all; one that isn't JSON says so; none at all has nothing to check.
        self.write(".claude/settings.json", '{"permissions": {"allow": []}}\n')
        self.assertEqual([p.split(" lacks ")[0].split(".")[-1] for p in self.problems()],
                         ["SessionStart", "PreToolUse", "PostToolUse", "Stop"])
        self.write(".claude/settings.json", '{"hooks": {\n')
        found = self.problems()
        self.assertEqual(len(found), 1, found)
        self.assertRegex(found[0], r"^\.claude/settings\.json: not JSON \(.+\): its hooks block can't be checked$")
        self.edit_config(lambda data: data.update(conform={"allow": [{"path": ".claude/settings.json",
                                                                      "why": "hooks of the app's own"}]}))
        self.assertEqual(self.problems(), [])
        self.edit_config(lambda data: data.pop("conform"))
        (self.root / ".claude/settings.json").unlink()
        (self.root / ".claude/hooks/hook.py").unlink()
        self.assertEqual(self.problems(), [])

    def test_every_agents_hooks_call_the_apps_wrapper(self):
        direct = SETTINGS.replace("$CLAUDE_PROJECT_DIR/.claude/hooks/hook.py", "$CLAUDE_PROJECT_DIR/laching/tools/hooks/hook.py")
        said = ("calls laching/tools/hooks/hook.py itself ({} hook(s)): call the app's wrapper, .claude/hooks/hook.py, which "
                "hands the hook the app's root (COMPANION_ROOT), as every agent's hooks do")
        wrapper = (".claude/hooks/hook.py: missing: the wrapper of laching/tools/hooks/hook.py the agents' hooks call (the "
                   "Python template in laching/tools/README.md, *Staying on Laching's tools*, with ../.. for the app's root)")
        self.settings(text=direct)
        self.assertEqual(self.problems(), [f".claude/settings.json: {said.format(4)}", wrapper],
                         "one line for the form, none for each hook the block then lacks; a permission isn't a call")
        self.settings()
        self.assertEqual(self.problems(), [wrapper])
        self.write(".claude/hooks/hook.py", HOOK_WRAPPER)
        self.assertEqual(self.problems(), [])

        # Codex's and Copilot's hooks call the same wrapper.
        codex = ("# The guardrails Claude Code runs: .claude/hooks/hook.py, a wrapper of laching/tools/hooks/hook.py.\n"
                 "[[hooks.PreToolUse.hooks]]\ntype = \"command\"\n"
                 "command = 'python3 \"$(git rev-parse --show-toplevel)/{}\" pre'\ntimeout = 10\n")
        copilot = '{{"version": 1, "hooks": {{"PreToolUse": [{{"type": "command", "bash": "python3 {} pre"}}],\n' \
                  ' "PostToolUse": [{{"type": "command", "powershell": "python3 {} post"}}]}}}}\n'
        self.write(".codex/config.toml", codex.format(".claude/hooks/hook.py"))
        self.write(".github/hooks/fixture.json", copilot.format(".claude/hooks/hook.py", ".claude\\\\hooks\\\\hook.py"))
        self.assertEqual(self.problems(), [], "a comment that names Laching's hook isn't a call")
        self.write(".codex/config.toml", codex.format("laching/tools/hooks/hook.py"))
        self.write(".github/hooks/fixture.json", copilot.format("laching/tools/hooks/hook.py",
                                                                "laching\\\\tools\\\\hooks\\\\hook.py"))
        self.assertEqual(sorted(self.problems()), [f".codex/config.toml: {said.format(1)}",
                                                   f".github/hooks/fixture.json: {said.format(2)}"])
        self.write(".github/hooks/fixture.json", copilot.format(*["laching/tools/hooks/hook.py"] * 2).replace("\n", ""))
        self.assertIn(f".github/hooks/fixture.json: {said.format(2)}", self.problems(), "each call on one line counts")
        self.write(".github/hooks/fixture.json", copilot.format("laching/tools/hooks/hook.py",
                                                                "laching\\\\tools\\\\hooks\\\\hook.py"))
        self.edit_config(lambda data: data.update(conform={"allow": [
            {"path": ".codex/config.toml", "why": "tried without the wrapper"},
            {"path": ".github/hooks/fixture.json", "why": "the same"}]}))
        self.assertEqual(self.problems(), [])

        # Wherever the app keeps Laching.
        self.edit_config(lambda data: (data.pop("conform"), data["laching"].update(path="vendor/laching")))
        self.write(".claude/hooks/hook.py", HOOK_WRAPPER.replace('"laching"', '"vendor", "laching"'))
        (self.root / ".github/hooks/fixture.json").unlink()
        self.write(".codex/config.toml", codex.format("vendor/laching/tools/hooks/hook.py"))
        self.assertEqual(self.problems(), [".codex/config.toml: " + said.format(1).replace("laching/", "vendor/laching/")])

    def test_skills_link_into_laching(self):
        git(self.root, "init", "-q")
        skills = self.root / ".agents" / "skills"
        # A copy of Laching's skill, the app's own skill, and links recorded the way git records them (mode 120000).
        self.write(".agents/skills/release-windows/SKILL.md", "# Release the Windows app, our way\n")
        self.write(".agents/skills/add-exercise/SKILL.md", "# The app's own\n")
        self.link(".agents/skills/localization", "../../laching/tools/skills/localization")
        self.link(".agents/skills/voice", "../../elsewhere/voice")
        git(self.root, "add", ".agents/skills/release-windows", ".agents/skills/add-exercise")
        found = sorted(p for p in self.problems() if p.startswith(".agents"))
        self.assertEqual(len(found), 2, found)
        self.assertIn(".agents/skills/release-windows: a copy of laching/tools/skills/release-windows: link it", found[0])
        self.assertIn(".agents/notes/release-windows.md", found[0])
        self.assertIn(".agents/skills/voice: links to ../../elsewhere/voice", found[1])
        self.assertTrue(skills.is_dir())

    def test_every_agent_finds_the_skills_through_links(self):
        git(self.root, "init", "-q")
        self.write(".agents/skills/add-exercise/SKILL.md", "# The app's own\n")
        self.link(".agents/skills/localization", "../../laching/tools/skills/localization")
        git(self.root, "add", ".agents/skills/add-exercise")
        self.assertEqual(self.problems(), [
            ".github/skills: missing: ln -s ../.agents/skills .github/skills",
            ".junie/skills: missing: ln -s ../.agents/skills .junie/skills",
            ".claude/skills/add-exercise: missing: ln -s ../../.agents/skills/add-exercise .claude/skills/add-exercise",
            ".claude/skills/localization: missing: ln -s ../../.agents/skills/localization .claude/skills/localization"])

        self.link(".github/skills", "../.agents/skills")
        self.link(".junie/skills", "../.agents/skills/")
        self.link(".claude/skills/add-exercise", "../../.agents/skills/add-exercise")
        self.link(".claude/skills/localization", "../../.agents/skills/localization")
        self.write(".claude/skills/claude-only/SKILL.md", "# Claude Code's alone\n")
        # In a checkout git records every link, so an untracked small file is a file, not a link to want.
        self.write(".agents/skills/README.md", "The app's skills.\n")
        self.assertEqual(self.problems(), [])

        self.link(".github/skills", "../skills")
        git(self.root, "update-index", "--force-remove", ".claude/skills/add-exercise")
        (self.root / ".claude/skills/add-exercise").unlink()
        self.write(".claude/skills/add-exercise/SKILL.md", "# A copy\n")
        git(self.root, "add", ".claude/skills/add-exercise")
        self.assertEqual(self.problems(), [
            ".github/skills: links to ../skills, not ../.agents/skills",
            ".claude/skills/add-exercise: a folder of its own: make it a link (ln -s ../../.agents/skills/add-exercise "
            ".claude/skills/add-exercise)"])

    def test_store_microsoft_as_the_schema_wants_it(self):
        self.edit_config(lambda data: data["store"].update(microsoft={"screenshotCount": 8, "publisher": "Fixture Studio"}))
        found = self.problems()
        self.assertIn("companion.json: store.microsoft: missing shots", found)
        self.assertIn("companion.json: store.microsoft: unknown key screenshotCount", found)
        # companion.py conform reports it with the rest, where every other command refuses the file.
        done = subprocess.run([sys.executable, str(TOOLS / "companion.py"), "conform"], cwd=self.root, capture_output=True,
                              encoding="utf8")
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

    def test_a_linked_app_is_named_and_only_the_pins_link_left_are_compared(self):
        git(self.laching, "tag", "-d", "v0.6.0")
        (self.laching / "Package.swift").write_text('let package = Package(name: "Laching")\n', "utf8")
        project = self.laching / "dotnet" / "Laching.Core" / "Laching.Core.csproj"
        project.parent.mkdir(parents=True)
        project.write_text("<Project><PropertyGroup><IsPackable>true</IsPackable></PropertyGroup></Project>\n", "utf8")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "app")
        with mock.patch.object(companion, "regenerate_project", return_value=""):
            linked, out = quiet(companion.link, companion.load(self.root), self.laching)
        self.assertEqual(linked, 0, out)
        self.assertNotIn("exactVersion", (self.root / "macos/project.yml").read_text("utf8"), "a pin that names no version")
        link = f"linked to {self.laching.resolve()} (companion.py unlink)"

        done, out = quiet(conform.apps, [self.root, self.other], self.laching)
        self.assertIn(f"{self.root.name}: v0.5.0 (newest, {link}, conforms)", out)
        self.assertNotIn("pins disagree", out, "the pins `link` pointed at the checkout aren't a disagreement")
        self.assertIn("other: v0.5.0 (newest, conforms)", out)
        self.assertEqual(done, 1, "a link is drift: " + out)

        core = self.root / "windows/Fixture.Core/Fixture.Core.csproj"
        core.write_text(core.read_text("utf8").replace('Version="0.5.0"', 'Version="0.4.0"'), "utf8")
        done, out = quiet(conform.apps, [self.root, self.other], self.laching)
        self.assertIn(f"{self.root.name}: v0.5.0 (newest, {link}, its pins disagree (companion.py pins), conforms)", out)
        self.assertEqual(done, 1)
