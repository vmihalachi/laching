#!/usr/bin/env python3
"""Tests for tools/windows/vm.sh (vm.py), the Parallels VM runner, without a VM: the files it mirrors, the tar it
sends, the paths the VM sees, the PowerShell it runs for each command, the one line it prints when there's no VM
(no prlctl, an unknown, stopped or suspended VM) or another run uses it against a fake `prlctl`, and one run at a time
on the VM from any checkout, with real vm.py processes whose VM side is faked. Where PowerShell is installed,
vm-guest.ps1's Sync-Tree runs over a folder: what it unpacks, deletes, keeps and asks for."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "windows"))
from support import FIXTURE_APP, TOOLS, WINDOWS, FixtureAppTestCase, git  # noqa: E402

if WINDOWS:
    raise unittest.SkipTest("vm.py runs on a Mac, driving its Parallels VM (it locks with fcntl, which Windows lacks)")
import vm  # noqa: E402

VM_SH = TOOLS / "windows" / "vm.sh"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "app"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name: str, text: str = "x", folder: Path | None = None) -> Path:
        path = (folder or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf8")
        return path

    def test_lists_what_git_sees_with_submodules(self):
        git(self.root, "init", "-q")
        self.write(".gitignore", "bin/\n*.log\n")
        self.write("windows/App/App.cs", "class A {}")
        self.write("windows/App/Gone.cs")
        self.write("macos/Model.swift")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "one")
        self.write("windows/App/App.cs", "class A { int b; }")  # modified, uncommitted
        self.write("windows/App/New.cs")  # untracked
        self.write("windows/App/bin/Debug/App.dll")  # ignored
        self.write("build.log")  # ignored
        (self.root / "windows/App/Gone.cs").unlink()  # deleted, uncommitted
        (self.root / "link.cs").symlink_to("windows/App/App.cs")
        self.write("odd\tname.cs")
        # A checked-out submodule: a repository of its own, recorded as a gitlink.
        sub = self.root / "laching"
        sub.mkdir()
        git(sub, "init", "-q")
        self.write("tools/windows/vm.py", folder=sub)
        git(sub, "add", ".")
        git(sub, "commit", "-qm", "tools")
        self.write("tools/windows/draft.py", folder=sub)  # untracked in the submodule
        head = git(sub, "rev-parse", "HEAD").strip()
        git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{head},laching")

        files = vm.list_files(self.root)
        names = [name for name, _, _ in files]
        self.assertEqual(names, sorted([
            ".gitignore", "laching/tools/windows/draft.py", "laching/tools/windows/vm.py", "macos/Model.swift",
            "windows/App/App.cs", "windows/App/New.cs"]))
        app = next(entry for entry in files if entry[0] == "windows/App/App.cs")
        stat = (self.root / "windows/App/App.cs").stat()
        self.assertEqual(app[1:], (stat.st_size, int(stat.st_mtime)))

    def test_the_tar_holds_whole_second_mtimes(self):
        path = self.write("windows/App/App.cs", "class A {}")
        os.utime(path, (1_700_000_000.75, 1_700_000_000.75))
        archive = self.root.parent / "sync.tar"
        vm.write_tar(self.root, ["windows/App/App.cs", "missing.cs"], archive)
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            self.assertEqual([m.name for m in members], ["windows/App/App.cs"])
            self.assertEqual(members[0].mtime, 1_700_000_000)
            self.assertEqual(tar.extractfile(members[0]).read(), b"class A {}")

    def test_the_list_is_tab_separated(self):
        vm.write_list([("a/b.cs", 3, 17), ("c.txt", 0, 18)], self.root / "files.tsv")
        self.assertEqual((self.root / "files.tsv").read_text("utf8"), "a/b.cs\t3\t17\nc.txt\t0\t18\n")


class PathTests(unittest.TestCase):
    def test_the_vm_sees_mac_paths_through_the_share(self):
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.realpath(temp)
            self.assertEqual(vm.guest_path(temp, r"\\Mac\AllFiles"), "\\\\Mac\\AllFiles" + real.replace("/", "\\"))
            os.environ["LACHING_VM_SHARE"] = "Z:\\"
            try:
                self.assertEqual(vm.guest_path(Path(temp) / "a b"), "Z:" + real.replace("/", "\\") + "\\a b")
            finally:
                del os.environ["LACHING_VM_SHARE"]

    def test_each_checkout_gets_its_own_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            one, two = Path(temp) / "my app", Path(temp) / "wt" / "my app"
            self.assertRegex(vm.checkout_key(one), r"^my-app-[0-9a-f]{6}$")
            self.assertEqual(vm.checkout_key(one), vm.checkout_key(one))
            self.assertNotEqual(vm.checkout_key(one), vm.checkout_key(two))

    def test_powershell_quoting_and_encoding(self):
        self.assertEqual(vm.ps("it's"), "'it''s'")
        script = "Write-Output 'ciao'"
        self.assertEqual(base64.b64decode(vm.encode(script)).decode("utf-16-le"), script)

    def test_platforms(self):
        self.assertEqual(vm.parse_platforms([]), [])
        self.assertEqual(vm.parse_platforms(["arm64", "ARM64", "X64"]), ["ARM64", "x64"])
        with self.assertRaises(vm.Usage):
            vm.parse_platforms(["x86"])

    def test_extra_arguments_follow_the_dashes(self):
        self.assertEqual(vm.split_extra(["x64", "--", "-c", "Release"]), (["x64"], "-c Release"))
        self.assertEqual(vm.split_extra(["ARM64"]), (["ARM64"], ""))


class BodyTests(FixtureAppTestCase):
    """The PowerShell each command runs in the VM, over the fixture app's companion.json."""

    def setUp(self):
        super().setUp()
        import companion

        config = companion.load(self.root)
        self.ctx = vm.Context(self.root, config, "Windows 11", vm.checkout_key(self.root), self.root / "stage")

    def test_build_both_platforms_with_extra_arguments(self):
        body = vm.build_body(self.ctx, ["x64", "ARM64"], "-c Release")
        project = r"'windows\Fixture.App\Fixture.App.csproj'"
        self.assertIn(f"dotnet build {project} -p:Platform=x64 -v q -nologo '-clp:NoSummary' -c Release", body)
        self.assertIn(f"dotnet build {project} -p:Platform=ARM64 -v q -nologo '-clp:NoSummary' -c Release", body)
        self.assertTrue(body.endswith("Exit-Steps"))

    def test_test_runs_the_test_projects(self):
        body = vm.test_body(self.ctx, "")
        self.assertIn(r"dotnet test 'windows\Fixture.App.Tests\Fixture.App.Tests.csproj'", body)
        self.assertNotIn("Core.Tests", body, "the fixture names no windows.coreTests")

    def test_screenshots_take_the_apps_wrapper_when_it_has_one(self):
        shots = Path("/tmp/shots")
        body = vm.screenshots_body(self.ctx, shots, "-Language it")
        self.assertIn(vm.guest_path(vm.HERE / "screenshots.ps1"), body, "Laching's, through the share")
        self.assertIn("-Out $shots -Language it", body)
        self.assertIn(f"Copy-Shots $shots {vm.ps(vm.guest_path(shots))}", body)
        wrapper = self.root / "windows" / "scripts" / "screenshots.ps1"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("& \"$PSScriptRoot/../../laching/tools/windows/screenshots.ps1\" @args\n", "utf8")
        self.assertIn(r"& 'windows\scripts\screenshots.ps1' -Out $shots", vm.screenshots_body(self.ctx, shots, ""))

    def test_check_layout_runs_the_languages_in_one_go_and_copies_each_folder(self):
        folder = Path("/tmp/layout")
        body = vm.check_layout_body(self.ctx, folder, ["fr", "de"], "-Only home,settings -Jobs 2")
        self.assertIn(vm.guest_path(vm.HERE / "check-layout.ps1"), body, "Laching's, through the share")
        self.assertIn("-Out $out 'fr' 'de' -Only home,settings -Jobs 2", body)
        self.assertIn(f"Copy-ShotFolders $out {vm.ps(vm.guest_path(folder))}", body)
        self.assertIn("-Out $out }", vm.check_layout_body(self.ctx, folder, [], ""), "languages.all when none")
        self.assertEqual(vm.layout_folder(self.ctx), self.root / "windows" / "build" / "layout-check")

    def test_smoke_publishes_then_runs_the_smoke_script(self):
        body = vm.smoke_body(self.ctx, None, "-SideBySide")
        self.assertIn("$platform = Get-GuestPlatform", body)
        self.assertIn("dotnet publish 'windows\\Fixture.App\\Fixture.App.csproj' -c Release -p:Platform=$platform", body)
        self.assertIn("-Package $msix.FullName -SideBySide", body)
        self.assertIn("$platform = 'x64'", vm.smoke_body(self.ctx, "x64", ""))

    def test_the_script_loads_the_guest_functions_from_the_share(self):
        script = vm.guest_script(self.ctx, "exit 0")
        self.assertIn(f". {vm.ps(vm.guest_path(vm.GUEST_SCRIPT))}", script)
        self.assertIn(f"Resolve-GuestFolder '%USERPROFILE%\\laching-vm\\{self.ctx.key}'", script)
        self.assertIn("catch", script)


class LinkedTests(FixtureAppTestCase):
    """The fixture app linked (`companion.py link`) to a Laching checkout: what else vm.sh mirrors, and where to."""

    def setUp(self):
        import companion

        super().setUp()
        self.companion = companion
        self.ctx = vm.Context(self.root, companion.load(self.root), "Windows 11", vm.checkout_key(self.root),
                              self.root.parent / "caches" / vm.checkout_key(self.root))

    def checkout(self, name: str = "laching") -> Path:
        """A Laching checkout beside the app: a git repository with a .NET project, a README and test resources."""
        folder = self.root.parent / name
        project = "<Project><PropertyGroup><IsPackable>true</IsPackable></PropertyGroup></Project>\n"
        for path, text in (("Package.swift", 'let package = Package(name: "Laching")\n'),
                           ("dotnet/Laching.Core/Laching.Core.csproj", project),
                           ("README.md", "# Laching\n"), ("Tests/CoreTests/Resources/vectors.json", "[]\n"),
                           (".gitignore", "bin/\nobj/\n")):
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_text(text, "utf8")
        git(folder, "init", "-q")
        git(folder, "add", ".")
        git(folder, "commit", "-qm", "laching")
        (folder / "dotnet/Laching.Core/obj/project.assets.json").parent.mkdir(parents=True)
        (folder / "dotnet/Laching.Core/obj/project.assets.json").write_text("{}", "utf8")  # ignored
        (folder / "dotnet/Laching.Core/Draft.cs").write_text("class D {}", "utf8")  # untracked
        return folder

    def link(self, checkout: Path) -> None:
        with mock.patch.object(self.companion, "regenerate_project", return_value=None), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.companion.link(self.ctx.config, checkout), 0)

    def write_targets(self, dotnet: str) -> None:
        (self.root / "windows/Directory.Build.targets").write_text(
            f"<Project>\n  <!-- laching link -->\n  <PropertyGroup>\n    <LachingLinkedDotnet>{dotnet}"
            "</LachingLinkedDotnet>\n  </PropertyGroup>\n</Project>\n", "utf8")

    def test_unlinked_mirrors_the_app_alone(self):
        self.assertIsNone(vm.linked_checkout(self.ctx))

    def test_a_checkout_beside_the_app_goes_where_its_reference_resolves(self):
        checkout = self.checkout()
        self.link(checkout)
        linked = vm.linked_checkout(self.ctx)
        self.assertEqual(linked.checkout, checkout)
        self.assertEqual(linked.guest, r"%USERPROFILE%\laching-vm\laching", r"windows\..\..\laching from the copy")
        self.assertEqual(linked.stage, self.ctx.stage.parent / "linked-laching", "shared by every app linked there")
        self.assertEqual([name for name, _, _ in vm.list_files(linked.checkout)], [
            ".gitignore", "Package.swift", "README.md", "Tests/CoreTests/Resources/vectors.json",
            "dotnet/Laching.Core/Draft.cs", "dotnet/Laching.Core/Laching.Core.csproj"])
        os.environ["LACHING_VM_DIR"] = "D:\\vm\\"
        try:
            self.assertEqual(vm.linked_checkout(self.ctx).guest, r"D:\vm\laching")
        finally:
            del os.environ["LACHING_VM_DIR"]

        with mock.patch.object(self.companion, "regenerate_project", return_value=None), \
                redirect_stdout(io.StringIO()):
            self.companion.unlink(self.ctx.config)
        self.assertIsNone(vm.linked_checkout(self.ctx))

    def test_another_folder_name_keeps_its_name(self):
        self.link(self.checkout("laching-wip"))
        self.assertEqual(vm.linked_checkout(self.ctx).guest, r"%USERPROFILE%\laching-vm\laching-wip")

    def test_sync_mirrors_the_app_then_the_linked_checkout(self):
        checkout = self.checkout()
        self.link(checkout)
        self.ctx.linked = vm.linked_checkout(self.ctx)
        with mock.patch.object(vm, "mirror", side_effect=lambda ctx, *where: (calls.append(where), "done")[1]), \
                redirect_stdout(io.StringIO()) as out:
            calls = []
            vm.sync(self.ctx)
        self.assertEqual(calls, [(self.root, self.ctx.guest_root, self.ctx.stage),
                                 (checkout, r"%USERPROFILE%\laching-vm\laching", self.ctx.linked.stage)])
        self.assertIn(f"the linked Laching {checkout}", out.getvalue())

    def test_a_checkout_inside_the_app_comes_with_it(self):
        self.write_targets("$(MSBuildThisFileDirectory)../laching/dotnet/")
        (self.root / "laching/dotnet").mkdir(parents=True)
        self.assertIsNone(vm.linked_checkout(self.ctx), "the app's own copy holds its submodule")

    def test_a_link_the_vm_cant_follow_is_refused(self):
        self.checkout()
        for dotnet, words in (("/Users/someone/laching/dotnet/", "a Mac path the VM doesn't have"),
                              ("$(MSBuildThisFileDirectory)../../../laching/dotnet/", "lands outside"),
                              ("$(MSBuildThisFileDirectory)../../dotnet/", "lands outside"),
                              ("$(MSBuildThisFileDirectory)../../elsewhere/dotnet/", "isn't there")):
            with self.subTest(dotnet=dotnet):
                self.write_targets(dotnet)
                with self.assertRaises(vm.Usage) as caught:
                    vm.linked_checkout(self.ctx)
                self.assertIn(words, str(caught.exception))
                self.assertIn("beside the app", str(caught.exception))


class UnavailableTests(FixtureAppTestCase):
    """vm.sh with a fake prlctl, or none: one line and exit 3 when there's no VM to use."""

    def setUp(self):
        super().setUp()
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()

    def prlctl(self, status: str, exec_code: int = 0) -> None:
        path = self.bin / "prlctl"
        path.write_text(f'#!/bin/sh\ncase "$1" in\n  status) {status} ;;\n  exec) exit {exec_code} ;;\n'
                        '  *) exit 9 ;;\nesac\n', "utf8")
        path.chmod(0o755)

    def run_vm(self, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": path or f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.root)}
        return subprocess.run([str(VM_SH), *args], cwd=self.root, env=env, capture_output=True, encoding="utf8")

    def test_without_prlctl(self):
        python = Path(sys.executable).parent
        for command in ("status", "build", "screenshots"):
            done = self.run_vm(command, *(["shots"] if command == "screenshots" else []),
                               path=f"{self.bin}:{python}:/usr/bin:/bin")
            if shutil.which("prlctl", path=f"{python}:/usr/bin:/bin"):
                self.skipTest("prlctl is in /usr/bin")
            self.assertEqual(done.returncode, vm.UNAVAILABLE, done.stderr)
            self.assertEqual(done.stderr.count("\n"), 1, done.stderr)
            self.assertIn("no prlctl here", done.stderr)

    def test_unknown_stopped_and_suspended_vms(self):
        self.prlctl('echo "Failed to get VM config: The virtual machine could not be found."; exit 255')
        done = self.run_vm("build")
        self.assertEqual(done.returncode, vm.UNAVAILABLE)
        self.assertIn("no VM named 'Windows 11'", done.stderr)

        self.prlctl('echo "VM $2 exist stopped"')
        done = self.run_vm("status", path=f"{self.bin}:{os.environ['PATH']}")
        self.assertEqual(done.returncode, vm.UNAVAILABLE)
        self.assertIn("'Windows 11' is stopped", done.stderr)

        self.prlctl('echo "VM $2 exist suspended"')
        done = self.run_vm("status")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("suspended; vm.sh resumes it", done.stdout)

    def test_running_needs_someone_signed_in(self):
        self.prlctl('echo "VM $2 exist running"', exec_code=1)
        done = self.run_vm("status")
        self.assertEqual(done.returncode, vm.UNAVAILABLE)
        self.assertIn("no one is signed in", done.stderr)
        self.prlctl('echo "VM $2 exist running"')
        done = self.run_vm("status")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "vm.sh: the VM 'Windows 11' is ready"))
        done = subprocess.run([str(VM_SH), "status"], cwd=self.root, capture_output=True, encoding="utf8",
                              env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "LACHING_VM": "Dev Box"})
        self.assertIn("'Dev Box' is ready", done.stdout)

    def test_a_lost_job_is_tried_again(self):
        count = self.root / "count"
        path = self.bin / "prlctl"
        path.write_text(f'''#!/bin/sh
n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{count}"
if [ $n -lt 3 ]; then echo "PrlJob_GetResult: Invalid argument. An invalid argument was passed." >&2; exit 255; fi
echo "ran $*"
''', "utf8")
        path.chmod(0o755)
        previous = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{previous}"
        try:
            done = vm.prlctl_exec(["Windows 11", "cmd", "/c", "exit 0"], capture=True)
            self.assertEqual((done.returncode, done.stdout.strip()), (0, "ran exec Windows 11 cmd /c exit 0"))
            self.assertEqual(count.read_text().strip(), "3")
            count.unlink()
            done = vm.prlctl_exec(["Windows 11", "cmd"], capture=True, attempts=2)
            self.assertEqual(done.returncode, 255, "gives up after the attempts")
            self.assertIn("PrlJob_GetResult", done.stderr)
        finally:
            os.environ["PATH"] = previous

    def test_status_names_the_run_that_uses_the_vm(self):
        self.prlctl('echo "VM $2 exist running"')
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            lock = vm.vm_lock("Windows 11")
        self.assertEqual(lock, self.root / "Library/Caches/laching-vm/Windows-11.lock")
        run = vm.owner(Path("/Users/me/wt/app"), ["check-layout", "fr", "--", "-Only", "home"])
        with vm.one_at_a_time(lock, "the VM 'Windows 11'", run):
            done = self.run_vm("status")
        self.assertEqual(done.returncode, vm.BUSY, done.stderr)
        self.assertEqual(done.stderr, "vm.sh: the VM 'Windows 11' is busy with vm.sh check-layout fr -- -Only home "
                                      f"from /Users/me/wt/app (pid {os.getpid()}, since {run['since']})\n")
        done = self.run_vm("status")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "vm.sh: the VM 'Windows 11' is ready"))

    def test_usage_mistakes(self):
        self.prlctl('echo "VM $2 exist running"')
        for args, words in ((["bogus"], "unknown command"), (["screenshots"], "one folder"),
                            (["run"], "after --"), (["build", "x86"], "unknown platform"),
                            (["test", "fast"], "ARGS go after --"),
                            (["smoke", "x64", "ARM64"], "one platform"),
                            (["check-layout", "-Only"], "takes languages")):
            with self.subTest(args=args):
                done = self.run_vm(*args)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(words, done.stderr)


class LockTests(unittest.TestCase):
    """One run at a time on the VM from any checkout: vm.py processes that take the real locks, with the VM's side
    (the readiness check, the sync and the PowerShell) faked. A run marks `<name>.started` once it holds the locks
    and ends when the test makes `<name>.done`."""

    FAKE = """
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import vm
marks, name = Path(os.environ["MARKS"]), os.environ["NAME"]

def powershell(ctx, body, capture=False):
    while not (marks / f"{name}.done").exists():
        time.sleep(0.05)
    if os.environ.get("FAIL"):
        raise RuntimeError("the build broke")
    return subprocess.CompletedProcess([], 0)

vm.ensure_ready = lambda vm_name: None
vm.sync = lambda ctx: (marks / f"{name}.started").touch()
vm.powershell = powershell
sys.exit(vm.main(sys.argv[1:]))
"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.marks = self.temp / "marks"
        self.marks.mkdir()
        self.lock = self.temp / "home/Library/Caches/laching-vm/Windows-11.lock"

    def checkout(self, name: str) -> Path:
        root = self.temp / name
        if not root.exists():
            shutil.copytree(FIXTURE_APP, root)
        return root

    def start(self, name: str, checkout: str, *args: str, **env: str) -> subprocess.Popen:
        root = self.checkout(checkout)
        with open(self.marks / f"{name}.err", "w") as err:
            process = subprocess.Popen(
                [sys.executable, "-c", self.FAKE, str(TOOLS / "windows"), *args], cwd=root, stdout=subprocess.DEVNULL,
                stderr=err, env={**os.environ, "HOME": str(self.temp / "home"), "COMPANION_ROOT": str(root),
                                 "LACHING_VM": "Windows 11", "MARKS": str(self.marks), "NAME": name, **env})
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)  # first, when a test fails with the run still waiting
        return process

    def wait_for(self, check, what: str):
        deadline = time.monotonic() + 30
        while not (found := check()):
            if time.monotonic() > deadline:
                self.fail(f"no {what} after 30 s")
            time.sleep(0.05)
        return found

    def started(self, name: str) -> None:
        self.wait_for((self.marks / f"{name}.started").exists, f"{name}.started")

    def said(self, name: str, text: str) -> str:
        """What run `name` printed, once it holds `text`."""
        return self.wait_for(lambda: text in (said := (self.marks / f"{name}.err").read_text()) and said, text)

    def finish(self, name: str, process: subprocess.Popen) -> int:
        (self.marks / f"{name}.done").touch()
        return process.wait(30)

    def test_a_run_from_another_checkout_waits_naming_the_run_it_waits_for(self):
        first = self.start("first", "app", "build", "x64")
        self.started("first")
        second = self.start("second", "wt-app", "test")
        run = f"vm.sh build x64 from {self.temp / 'app'} (pid {first.pid}, since "
        said = self.said("second", "waiting")
        self.assertRegex(said, f"^vm.sh: the VM 'Windows 11' is busy with {re.escape(run)}"
                               r"\d\d:\d\d:\d\d\); waiting\n$")
        self.assertIn(run, vm.busy(self.lock), "status says the same")
        time.sleep(1)
        self.assertFalse((self.marks / "second.started").exists(), "the second run waits")

        self.assertEqual(self.finish("first", first), 0)
        self.started("second")
        self.assertIn(f"vm.sh test from {self.temp / 'wt-app'}", vm.busy(self.lock))
        self.assertEqual(self.finish("second", second), 0)
        self.assertIsNone(vm.busy(self.lock))

    def test_the_locks_go_when_a_run_fails_or_is_killed(self):
        first = self.start("first", "app", "smoke", FAIL="1")
        self.started("first")
        second = self.start("second", "app", "build")
        said = self.said("second", "waiting")
        self.assertIn(f"vm.sh: this checkout is busy with vm.sh smoke from {self.temp / 'app'} (pid {first.pid}", said)
        self.assertEqual(self.finish("first", first), 1)
        self.assertIn("RuntimeError: the build broke", (self.marks / "first.err").read_text())

        self.started("second")
        second.kill()
        second.wait(30)
        self.assertIsNone(vm.busy(self.lock))
        third = self.start("third", "wt-app", "build")
        self.started("third")
        self.assertEqual(self.finish("third", third), 0)
        self.assertEqual((self.marks / "third.err").read_text(), "", "no wait")

    def test_one_at_a_time_holds_the_lock_for_the_block(self):
        run = vm.owner(Path("/Users/me/app"), ["build"])
        self.assertIsNone(vm.busy(self.lock), "no lock file yet")
        with self.assertRaises(RuntimeError):
            with vm.one_at_a_time(self.lock, "the VM 'Windows 11'", run):
                self.assertEqual(vm.busy(self.lock), f"vm.sh build from /Users/me/app (pid {os.getpid()}, "
                                                     f"since {run['since']})")
                raise RuntimeError
        self.assertIsNone(vm.busy(self.lock))
        self.assertEqual(json.loads(self.lock.read_text()), run, "left as it was: no one reads it unless it's held")
        shorter = vm.owner(Path("/a"), ["sync"])
        with vm.one_at_a_time(self.lock, "the VM 'Windows 11'", shorter):
            self.assertEqual(self.lock.read_text(), json.dumps(shorter) + "\n", "the last run's line is gone whole")
        self.lock.write_text("not json")
        fd = os.open(self.lock, os.O_RDONLY)
        try:
            self.assertEqual(vm.holder(fd), "another vm.sh run")
        finally:
            os.close(fd)


@unittest.skipUnless(POWERSHELL, "needs PowerShell")
class SyncTreeTests(unittest.TestCase):
    """vm-guest.ps1's Sync-Tree over a folder: unpack, delete what the Mac no longer has, keep bin/obj, list the rest."""

    def test_sync_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp).resolve()
            mac, root, stage = temp / "mac", temp / "vm", temp / "stage"
            for folder in (mac, root, stage):
                folder.mkdir()
            for name, text in (("a/App.cs", "new"), ("b.txt", "same"), ("c.txt", "later")):
                (mac / name).parent.mkdir(parents=True, exist_ok=True)
                (mac / name).write_text(text, "utf8")
                os.utime(mac / name, (1_700_000_000, 1_700_000_000))
            vm.write_tar(mac, ["a/App.cs", "b.txt"], stage / "sync.tar")
            for name in ("a/App.cs", "stale/Old.cs", "obj/project.assets.json", "a/bin/App.dll"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("old", "utf8")
            vm.write_list([(n, (mac / n).stat().st_size, 1_700_000_000) for n in ("a/App.cs", "b.txt", "c.txt")],
                          stage / "files.tsv")
            done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                                   f". {vm.ps(str(vm.GUEST_SCRIPT))}; Sync-Tree {vm.ps(str(root))} {vm.ps(str(stage))}"],
                                  capture_output=True, encoding="utf8")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn(f"synced\t{root}\t1", done.stdout)
            self.assertEqual((root / "a/App.cs").read_text("utf8"), "new")
            self.assertEqual((root / "b.txt").read_text("utf8"), "same")
            self.assertFalse((root / "stale").exists(), "a folder left empty goes too")
            self.assertTrue((root / "obj/project.assets.json").exists())
            self.assertTrue((root / "a/bin/App.dll").exists())
            self.assertEqual((stage / "need.txt").read_text("utf8").split(), ["c.txt"])


if __name__ == "__main__":
    unittest.main()
