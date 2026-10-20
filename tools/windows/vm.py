#!/usr/bin/env python3
r"""Builds and checks a companion app's Windows side in a Parallels Windows VM on this Mac, from the working tree as it is
(uncommitted changes included), so the WinUI app is built and looked at in the same session as the change instead of
in the app's Windows workflow. `vm.sh` runs this file.

    laching/tools/windows/vm.sh status                         # can the VM run commands? One line; exit 0 when it can,
                                                                # 4 while another run uses it
    laching/tools/windows/vm.sh sync                           # mirror the working tree (and a linked Laching)
    laching/tools/windows/vm.sh build [x64|ARM64…] [-- ARGS]   # the WinUI app (windows.app), Debug; both platforms
    laching/tools/windows/vm.sh test [-- ARGS]                 # windows.coreTests and windows.appTests
    laching/tools/windows/vm.sh screenshots DIR [-- ARGS]      # the screenshot script; the PNGs land in DIR on the Mac
    laching/tools/windows/vm.sh check-layout [LANG…] [-- ARGS] # check-layout.ps1: one build, the languages side by side;
                                                                # the PNGs land in <windows.dir>/build/layout-check/<LANG>
    laching/tools/windows/vm.sh smoke [x64|ARM64] [-- ARGS]    # publish the MSIX (Native AOT), then smoke-package.ps1
    laching/tools/windows/vm.sh run -- COMMAND…                # a PowerShell command in the VM's copy

ARGS go as they are, in PowerShell syntax, to `dotnet build`, `dotnet test`, the screenshot script (`-Language fr`,
`-Only home,settings`), check-layout.ps1 (`-Only home,settings`, `-Jobs 2`) or smoke-package.ps1 (`-SideBySide`). Those
three scripts are the app's own (`<windows.dir>/scripts/screenshots.ps1`, `check-layout.ps1`, `smoke-package.ps1`) when
it has them, else Laching's beside this file. `screenshots`, `check-layout` and `smoke` run for the VM's own platform
unless `-Platform` / a platform is given. `check-layout` runs every language in companion.json's `languages.all` unless
some are named, and replaces their folders' shots and problems.txt on the Mac.

The copy: the files git sees (tracked and untracked but not ignored, submodules included, symlinks left out) are
mirrored into `%USERPROFILE%\laching-vm\<checkout>-<hash>` in the VM through the Mac's shared folders. The VM lists
the files it lacks or holds in another version (size, mtime), the Mac sends a tar of just those, and the VM unpacks
it and deletes the files the Mac no longer has, leaving build output (bin, obj) alone so builds stay incremental. Every
command syncs first.

While the app is linked to a Laching checkout (`companion.py link`), its Windows projects build that checkout's
`dotnet/` projects, which they find by a relative path (`../../laching/dotnet/`). So that checkout's files (again what
git sees: the projects read its README and test resources outside `dotnet/` too) are mirrored the same way, to the VM
folder the path resolves to from the copy: `%USERPROFILE%\laching-vm\laching` for the usual `../laching` beside the
app. A link the VM can't follow (an absolute path, or one that leaves LACHING_VM_DIR) is refused; link a checkout
beside the app instead.

One vm.sh run at a time per VM, per checkout and per VM folder of a linked Laching, from any app or worktree: another
waits, naming the run it waits for (its command and checkout, which a run writes in the lock files it holds, under
~/Library/Caches/laching-vm). While a run uses the VM, `status` names it and exits 4, so a caller can skip instead.

Environment:
  LACHING_VM        the VM's name in Parallels ("Windows 11")
  LACHING_VM_SHARE  the VM's path to the Mac's / (\\Mac\AllFiles, Parallels' "All Files" share)
  LACHING_VM_DIR    where the copies go in the VM (%USERPROFILE%\laching-vm)

Without prlctl or the VM (Linux, a cloud session, a Mac without Parallels) every command prints one line saying so and
exits 3, so a caller skips instead of failing. A suspended or paused VM is resumed; a stopped one is left alone.
Commands run as the user signed in to Windows (`prlctl exec --current-user`), who sees the shares and the desktop.
Each one's PowerShell goes to the VM as a script file beside the run's lock files, which the VM reads through the share
(`powershell.exe -File`), since prlctl exec refuses a command line past about 4 KB (a long `-Only`, say).
Standard library only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import ntpath
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402

HERE = Path(__file__).resolve().parent
GUEST_SCRIPT = HERE / "vm-guest.ps1"
UNAVAILABLE = 3
BUSY = 4
PLATFORMS = ("x64", "ARM64")
COMMANDS = ("status", "sync", "build", "test", "screenshots", "check-layout", "smoke", "run")


class Unavailable(Exception):
    """The VM can't run commands here: no prlctl, no VM by that name, a stopped VM or no one signed in."""


class Usage(Exception):
    """A mistake in the command line or a command companion.json has nothing for."""


class Busy(Exception):
    """Another vm.sh run uses the VM, which `status` reports so a caller can skip instead of waiting."""


def vm_name() -> str:
    return os.environ.get("LACHING_VM") or "Windows 11"


def share_root() -> str:
    return (os.environ.get("LACHING_VM_SHARE") or r"\\Mac\AllFiles").rstrip("\\")


def guest_base() -> str:
    return (os.environ.get("LACHING_VM_DIR") or r"%USERPROFILE%\laching-vm").rstrip("\\")


def guest_path(mac_path: Path | str, share: str | None = None) -> str:
    """The VM's path to a Mac file or folder, through the share that shows the Mac's /."""
    return (share or share_root()) + os.path.realpath(mac_path).replace("/", "\\")


def checkout_key(root: Path) -> str:
    """The VM folder's name for a checkout: its folder's name and a hash of its path, so worktrees get their own."""
    digest = hashlib.sha1(os.path.realpath(root).encode()).hexdigest()[:6]
    return f"{re.sub(r'[^A-Za-z0-9._-]', '-', Path(root).name) or 'app'}-{digest}"


def ps(text: str) -> str:
    """`text` as a single-quoted PowerShell string."""
    return "'" + text.replace("'", "''") + "'"


def windows_rel(path: str) -> str:
    return path.replace("/", "\\")


# MARK: The files to mirror


def list_files(root: Path, prefix: str = "") -> list[tuple[str, int, int]]:
    """What git sees in `root`, as (path from the outermost root, size, mtime in whole seconds): tracked files and
    untracked ones that aren't ignored, in every checked-out submodule too. Symlinks, deleted files and paths with a
    tab or a line break are left out."""

    staged = companion.git(root, "ls-files", "-z", "--stage", check=True, text=False).stdout
    submodules = {os.fsdecode(entry.split(b"\t", 1)[1]) for entry in staged.split(b"\0") if entry.startswith(b"160000 ")}
    seen = companion.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", check=True, text=False)
    found: dict[str, tuple[str, int, int]] = {}
    for raw in seen.stdout.split(b"\0"):
        name = os.fsdecode(raw)
        if not name or name in found or "\t" in name or "\n" in name or "\r" in name:
            continue
        path = root / name
        if name in submodules:
            if (path / ".git").exists():
                for entry in list_files(path, f"{prefix}{name}/"):
                    found[entry[0]] = entry
            continue
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        found[prefix + name] = (prefix + name, stat.st_size, int(stat.st_mtime))
    return sorted(found.values())


def write_list(files: list[tuple[str, int, int]], path: Path) -> None:
    path.write_text("".join(f"{name}\t{size}\t{mtime}\n" for name, size, mtime in files), "utf8")


def write_tar(root: Path, names: list[str], archive: Path) -> None:
    """The files `names` (relative to `root`) in a tar whose mtimes are whole seconds, as the VM compares them."""
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
        for name in names:
            path = root / name
            try:
                info = tar.gettarinfo(str(path), arcname=name)
                with open(path, "rb") as handle:
                    info.mtime = int(info.mtime)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    tar.addfile(info, handle)
            except FileNotFoundError:
                continue  # gone since the listing: the VM's check after unpacking reports it


# MARK: The VM


@dataclass
class Linked:
    """The Laching checkout the app's Windows projects build while linked, and where the VM's copy of it goes."""

    checkout: Path
    guest: str  # the VM folder the app's relative reference resolves to from the app's copy
    stage: Path


@dataclass
class Context:
    root: Path
    config: companion.Config
    vm: str
    key: str
    stage: Path  # on the Mac, seen by the VM through the share
    linked: Linked | None = None

    @property
    def guest_root(self) -> str:
        return f"{guest_base()}\\{self.key}"

    @property
    def guest_out(self) -> str:
        return f"{guest_base()}\\{self.key}.out"

    def guest_tool(self, path: Path) -> str:
        """The VM's path to a file of this checkout's: inside the copy when it's in the checkout, else through the share."""
        try:
            return windows_rel(path.resolve().relative_to(self.root.resolve()).as_posix())
        except ValueError:
            return guest_path(path)

    def windows_project(self, key: str) -> str | None:
        """`<windows.dir>/<name>/<name>.csproj` for the project companion.json names at `key`, relative to the root."""
        name = self.config.get(key)
        folder = self.config.get("windows.dir")
        if not name or not folder:
            return None
        return f"{folder}/{name}/{name}.csproj"

    def script(self, name: str) -> str:
        """The app's own `<windows.dir>/scripts/<name>` when it has one, else Laching's beside this file."""
        folder = self.config.path("windows.dir")
        own = folder / "scripts" / name if folder else None
        return self.guest_tool(own if own and own.is_file() else HERE / name)


def linked_checkout(ctx: Context) -> Linked | None:
    """The Laching checkout the app's Windows projects build while linked (`companion.py link`), with the VM folder
    their relative reference resolves to from the app's copy, as MSBuild resolves it there. None when they build the
    pinned packages, or when the checkout is inside the app's own (its copy carries it). Raises Usage when the
    reference leads nowhere the VM's copies may go."""
    reference = companion.linked_dotnet(ctx.config)
    windows = ctx.config.path("windows.dir")
    if reference is None or windows is None:
        return None
    advice = "link a Laching checkout beside the app (`companion.py unlink`, then `link`)"
    if os.path.isabs(reference):
        raise Usage(f"the Windows projects are linked to {reference}, a Mac path the VM doesn't have: {advice}")
    dotnet = (windows / reference).resolve()
    from_base = ntpath.normpath("\\".join((ctx.key, windows_rel(windows.relative_to(ctx.root).as_posix()),
                                           windows_rel(reference))))
    folder = ntpath.dirname(from_base)
    if folder == ctx.key or folder.startswith(ctx.key + "\\"):
        return None
    if not folder or folder == ".." or folder.startswith("..\\"):
        raise Usage(f"the Windows projects are linked to {dotnet}, which lands outside {guest_base()} in the VM: "
                    f"{advice}")
    if not dotnet.is_dir():
        raise Usage(f"the Windows projects are linked to {dotnet}, which isn't there: {advice}")
    stage = ctx.stage.parent / f"linked-{re.sub(r'[^A-Za-z0-9._-]', '-', folder)}"
    return Linked(dotnet.parent, f"{guest_base()}\\{folder}", stage)


def prlctl(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["prlctl", *args], stdin=subprocess.DEVNULL, capture_output=capture, text=True)


# What prlctl exec prints, now and then, when it loses a job in Parallels Tools: the command may not have run at all.
LOST_JOB = re.compile(r"^PrlJob_\w+: .*$", re.M)
# What prlctl exec says, with exit code 255, when it can't start the command in the VM: a VM still booting or no one
# signed in, but also a command line past about 4 KB, so past LONG_COMMAND characters it's put down to the length.
NEW_SESSION = "Unable to open new session"
LONG_COMMAND = 3500


def too_long(args: list[str], code: int, stderr: str) -> str | None:
    """What to say when prlctl exec refused `args` for their length, which it words as a VM that isn't ready."""
    length = len(" ".join(args))
    if code != 255 or NEW_SESSION not in stderr or length <= LONG_COMMAND:
        return None
    return (f"vm.sh: prlctl exec refused a command line of {length} characters (it takes about 4,000): its "
            f"{NEW_SESSION!r} is about the length, not a VM that isn't ready or signed in\n")


def prlctl_exec(args: list[str], capture: bool, attempts: int = 3) -> subprocess.CompletedProcess:
    """`prlctl exec` with its output streamed (unless `capture`) and kept; tried again after a lost job that printed
    nothing, which prlctl reports with exit code 255 and a PrlJob_ line."""
    for attempt in range(1, attempts + 1):
        process = subprocess.Popen(["prlctl", "exec", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = [], []

        def pump(stream, kept: list, echo):
            for line in iter(stream.readline, b""):
                kept.append(line)
                if echo is not None and not LOST_JOB.match(line.decode("utf-8", "replace")):
                    echo.write(line)
                    echo.flush()

        threads = [threading.Thread(target=pump, args=(process.stdout, out, None if capture else sys.stdout.buffer)),
                   threading.Thread(target=pump, args=(process.stderr, err, None if capture else sys.stderr.buffer))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        process.wait()
        stdout, stderr = (b"".join(lines).decode("utf-8", "replace") for lines in (out, err))
        lost = process.returncode == 255 and LOST_JOB.search(stderr)
        if lost and not stdout.strip() and attempt < attempts:
            time.sleep(2 * attempt)
            continue
        if lost and not capture:
            print(f"vm.sh: prlctl lost the command in the VM ({LOST_JOB.search(stderr).group(0)}); run it again",
                  file=sys.stderr)
        if hint := too_long(args, process.returncode, stderr):
            stderr += hint
            if not capture:
                sys.stderr.write(hint)
                sys.stderr.flush()
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    raise AssertionError("unreachable")


def vm_state(vm: str) -> str:
    """running, suspended, paused, stopped…; raises Unavailable without prlctl or the VM."""
    if not shutil.which("prlctl"):
        raise Unavailable("no prlctl here (Parallels Desktop on a Mac), so no Windows VM to build in")
    done = prlctl("status", vm)
    match = re.search(r"\bexists?\s+(\w+)", done.stdout)
    if done.returncode or not match:
        raise Unavailable(f"Parallels has no VM named {vm!r} (set LACHING_VM to its name)")
    return match.group(1)


def signed_in(vm: str) -> bool:
    return prlctl_exec([vm, "--current-user", "cmd", "/c", "exit 0"], capture=True).returncode == 0


def ensure_ready(vm: str) -> None:
    """Resumes a suspended or paused VM and waits for the signed-in user; raises Unavailable otherwise."""
    state = vm_state(vm)
    deadline = time.monotonic()
    if state in ("suspended", "paused"):
        print(f"vm.sh: resuming the VM {vm!r} ({state})", file=sys.stderr)
        prlctl("resume", vm)
        state = vm_state(vm)
        deadline += 90
    if state != "running":
        raise Unavailable(f"the VM {vm!r} is {state}: start it in Parallels Desktop and sign in to Windows")
    while not signed_in(vm):
        if time.monotonic() > deadline:
            raise Unavailable(f"the VM {vm!r} is running but no one is signed in to Windows")
        time.sleep(3)


def status(vm: str) -> str:
    """One line on whether vm.sh can use the VM; raises Unavailable when it can't and Busy while another run uses it."""
    state = vm_state(vm)
    if state not in ("running", "suspended", "paused"):
        raise Unavailable(f"the VM {vm!r} is {state}: start it in Parallels Desktop and sign in to Windows")
    if run := busy(vm_lock(vm)):
        raise Busy(f"the VM {vm!r} is busy with {run}")
    if state != "running":
        return f"the VM {vm!r} is {state}; vm.sh resumes it"
    if not signed_in(vm):
        raise Unavailable(f"the VM {vm!r} is running but no one is signed in to Windows")
    return f"the VM {vm!r} is ready"


def guest_script(ctx: Context, body: str) -> str:
    """A PowerShell script for the VM: vm-guest.ps1's functions, the copy as the current folder, then `body`, which
    ends in `exit`. An exception exits 1 with its message."""
    return "\n".join((
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false } catch { }",
        "$env:DOTNET_CLI_UI_LANGUAGE = 'en'; $env:DOTNET_NOLOGO = '1'; $env:MSBUILDDISABLENODEREUSE = '1'",
        "try {",
        f". {ps(guest_path(GUEST_SCRIPT))}",
        f"$Root = Resolve-GuestFolder {ps(ctx.guest_root)}",
        "Set-Location -LiteralPath $Root",
        body,
        "} catch { [Console]::Error.WriteLine(\"vm.sh: $($_.Exception.Message)\"); exit 1 }",
    ))


def powershell(ctx: Context, body: str, capture: bool = False) -> subprocess.CompletedProcess:
    """Runs `body` (see guest_script) in the VM as the signed-in user; the output streams unless `capture`. The script
    goes as a file in the stage, which the VM runs through the share, so the command line stays short however long
    `body` is (see LONG_COMMAND); the file goes once the run ends."""
    ctx.stage.mkdir(parents=True, exist_ok=True)
    # UTF-8 with a BOM, which Windows PowerShell 5.1 reads as UTF-8 (without one, as the ANSI code page).
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", prefix="run-", suffix=".ps1", dir=ctx.stage,
                                     delete=False) as file:
        file.write(guest_script(ctx, body))
    script = Path(file.name)
    try:
        return prlctl_exec([ctx.vm, "--current-user", "powershell.exe", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-File", guest_path(script)], capture)
    finally:
        script.unlink(missing_ok=True)


# MARK: One run at a time


def caches() -> Path:
    return Path.home() / "Library" / "Caches" / "laching-vm"


def vm_lock(vm: str) -> Path:
    """The lock every checkout's runs take for the VM `vm`, beside the checkouts' folders."""
    return caches() / f"{re.sub(r'[^A-Za-z0-9._-]', '-', vm)}.lock"


def owner(root: Path, argv: list[str]) -> dict:
    """What a run writes in the lock files it holds, so a run that waits can say whose it waits for."""
    return {"checkout": str(root), "command": shlex.join(["vm.sh", *argv]), "pid": os.getpid(),
            "since": time.strftime("%H:%M:%S")}


def holder(fd: int) -> str:
    """The run the open lock file `fd` names (see owner): its command, its checkout, its process and its start."""
    try:
        run = json.loads(os.pread(fd, 65536, 0).decode("utf8").split("\n", 1)[0])
        return f"{run['command']} from {run['checkout']} (pid {run['pid']}, since {run['since']})"
    except (ValueError, KeyError, TypeError):
        return "another vm.sh run"  # one that hasn't written itself in yet, or an older vm.sh's


def busy(lock: Path) -> str | None:
    """The run holding the lock file `lock` (see holder), or None while none does."""
    try:
        fd = os.open(lock, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return holder(fd)
    finally:
        os.close(fd)
    return None


@contextmanager
def one_at_a_time(lock: Path, what: str, run: dict, poll: float = 0.5):
    """Holds the lock file `lock` while the block runs, with `run` (see owner) written in it. While another run holds
    it, says which one (again when that changes) and waits. The lock goes with the process, however that ends."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        said = None
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if (other := holder(fd)) != said:
                    print(f"vm.sh: {what} is busy with {other}; waiting", file=sys.stderr, flush=True)
                    said = other
                time.sleep(poll)
        # One line, over the last holder's before cutting what's left of it, so a reader sees one or the other whole.
        line = (json.dumps(run) + "\n").encode()
        os.pwrite(fd, line, 0)
        os.ftruncate(fd, len(line))
        yield
    finally:
        os.close(fd)


# MARK: Commands


def mirror(ctx: Context, source: Path, guest: str, stage: Path) -> str:
    """Mirrors what git sees in `source` into the VM's folder `guest` (see the module's docstring), through `stage`;
    returns what it did."""
    try:
        files = list_files(source)
    except subprocess.CalledProcessError as error:
        sys.exit(f"vm.sh: git can't list the files of {source}: {error.stderr.decode(errors='replace').strip()}")
    write_list(files, stage / "files.tsv")
    archive, need_file = stage / "sync.tar", stage / "need.txt"

    def round_trip() -> tuple[list[str], str, int]:
        need_file.unlink(missing_ok=True)
        done = powershell(ctx, f"Sync-Tree (Resolve-GuestFolder {ps(guest)}) {ps(guest_path(stage))}\nexit 0",
                          capture=True)
        match = re.search(r"^synced\t(.+)\t(\d+)\s*$", done.stdout, re.M)
        if done.returncode or not match or not need_file.exists():
            sys.exit(f"vm.sh: the copy into the VM failed:\n{done.stdout[-2000:]}{done.stderr[-2000:]}")
        return [line for line in need_file.read_text("utf-8-sig").splitlines() if line], match.group(1), int(match.group(2))

    archive.unlink(missing_ok=True)
    need, where, removed = round_trip()
    if need:
        write_tar(source, need, archive)
        missing, _, _ = round_trip()
        archive.unlink(missing_ok=True)
        if missing:
            sys.exit(f"vm.sh: {len(missing)} files didn't arrive in the VM, e.g. {missing[0]}")
    return f"{len(files)} files in {ctx.vm}:{where} ({len(need)} copied, {removed} removed)"


def sync(ctx: Context) -> None:
    """Mirrors the working tree into the VM's copy, then a linked Laching checkout (see the module's docstring)."""
    print(f"vm.sh: {mirror(ctx, ctx.root, ctx.guest_root, ctx.stage)}", flush=True)
    if ctx.linked:
        done = mirror(ctx, ctx.linked.checkout, ctx.linked.guest, ctx.linked.stage)
        print(f"vm.sh: {done}, the linked Laching {ctx.linked.checkout}", flush=True)


def parse_platforms(words: list[str]) -> list[str]:
    chosen = []
    for word in words:
        match = next((p for p in PLATFORMS if p.lower() == word.lower()), None)
        if match is None:
            raise Usage(f"unknown platform {word!r} (x64 or ARM64)")
        chosen.append(match)
    return list(dict.fromkeys(chosen))


def step(label: str, command: str) -> str:
    return f"Invoke-Step {ps(label)} {{ {command} }}"


def build_body(ctx: Context, platforms: list[str], extra: str) -> str:
    project = ctx.windows_project("windows.app")
    if not project:
        raise Usage("companion.json has no windows.app to build")
    lines = [step(f"dotnet build {project} ({platform}, Debug)",
                  f"dotnet build {ps(windows_rel(project))} -p:Platform={platform} -v q -nologo '-clp:NoSummary' {extra}".rstrip())
             for platform in platforms]
    return "\n".join([*lines, "Exit-Steps"])


def test_body(ctx: Context, extra: str) -> str:
    projects = [p for p in (ctx.windows_project("windows.coreTests"), ctx.windows_project("windows.appTests")) if p]
    if not projects:
        raise Usage("companion.json names no windows.coreTests or windows.appTests")
    lines = [step(f"dotnet test {project}", f"dotnet test {ps(windows_rel(project))} -v q --nologo {extra}".rstrip())
             for project in projects]
    return "\n".join([*lines, "Exit-Steps"])


def screenshots_body(ctx: Context, mac_dir: Path, extra: str) -> str:
    script, shots = ctx.script("screenshots.ps1"), ctx.guest_out + "\\shots"
    return "\n".join((
        f"$shots = Reset-GuestFolder {ps(shots)}",
        "try {",
        f"  {step(f'screenshots ({script})', f'& {ps(script)} -Out $shots {extra}'.rstrip())}",
        f"}} finally {{ Copy-Shots $shots {ps(guest_path(mac_dir))} }}",
        "Exit-Steps",
    ))


def check_layout_body(ctx: Context, mac_dir: Path, languages: list[str], extra: str) -> str:
    """check-layout.ps1 into a folder of the VM's own, then each language's shots and problems.txt into `mac_dir`."""
    script, out = ctx.script("check-layout.ps1"), ctx.guest_out + "\\layout-check"
    command = " ".join([f"& {ps(script)} -Out $out", *(ps(language) for language in languages), *([extra] if extra else [])])
    return "\n".join((
        f"$out = Reset-GuestFolder {ps(out)}",
        "try {",
        f"  {step(f'check-layout ({script})', command)}",
        f"}} finally {{ Copy-ShotFolders $out {ps(guest_path(mac_dir))} }}",
        "Exit-Steps",
    ))


def layout_folder(ctx: Context) -> Path:
    """Where `check-layout` puts the shots on the Mac: `<windows.dir>/build/layout-check`, as check-layout.ps1 does."""
    folder = ctx.config.path("windows.dir")
    if not folder:
        raise Usage("companion.json has no windows.dir")
    return folder / "build" / "layout-check"


def smoke_body(ctx: Context, platform: str | None, extra: str) -> str:
    project = ctx.windows_project("windows.app")
    if not project:
        raise Usage("companion.json has no windows.app to package")
    script, packages = ctx.script("smoke-package.ps1"), ctx.guest_out + "\\packages\\"
    return "\n".join((
        f"$platform = {ps(platform) if platform else 'Get-GuestPlatform'}",
        f"$packages = Reset-GuestFolder ({ps(packages)} + $platform)",
        "Add-VsWhere",
        step(f"dotnet publish {project} (Release, Native AOT)",
             f"dotnet publish {ps(windows_rel(project))} -c Release -p:Platform=$platform "
             "-p:GenerateAppxPackageOnBuild=true -p:AppxPackageSigningEnabled=false -p:AppxBundle=Never "
             "\"-p:AppxPackageDir=$packages/\" -v q -nologo '-clp:NoSummary'"),
        "if ($global:VmFailures.Count) { Exit-Steps }",
        "$msix = Find-Package $packages",
        step(f"smoke ({script})", f"& {ps(script)} -Package $msix.FullName {extra}".rstrip()),
        "Exit-Steps",
    ))


def split_extra(args: list[str]) -> tuple[list[str], str]:
    """The words before `--`, and the ones after it joined as PowerShell text."""
    if "--" in args:
        index = args.index("--")
        return args[:index], " ".join(args[index + 1:])
    return args, ""


def command_body(ctx: Context, command: str, words: list[str], extra: str) -> str | None:
    """The PowerShell `command` runs in the VM after the sync (None for sync alone); raises Usage on a mistake."""
    if command in ("sync", "test", "run") and words:
        raise Usage(f"{command} takes no {words[0]!r} (ARGS go after --)")
    if command == "sync":
        return None
    if command == "build":
        return build_body(ctx, parse_platforms(words) or list(PLATFORMS), extra)
    if command == "test":
        return test_body(ctx, extra)
    if command == "screenshots":
        if len(words) != 1:
            raise Usage("screenshots needs one folder on the Mac for the PNGs (ARGS go after --)")
        return screenshots_body(ctx, shots_folder(words[0]), extra)
    if command == "check-layout":
        if any(word.startswith("-") for word in words):
            raise Usage("check-layout takes languages (ARGS go after --)")
        return check_layout_body(ctx, layout_folder(ctx), words, extra)
    if command == "smoke":
        platforms = parse_platforms(words)
        if len(platforms) > 1:
            raise Usage("smoke takes one platform")
        return smoke_body(ctx, platforms[0] if platforms else None, extra)
    if command == "run":
        if not extra:
            raise Usage("run needs a command after --")
        return f"$global:LASTEXITCODE = 0\n{extra}\nexit $LASTEXITCODE"
    raise Usage(f"unknown command {command!r} ({', '.join(COMMANDS)}; --help)")


def shots_folder(word: str) -> Path:
    return Path(word).expanduser().resolve()


def clear_shots(folder: Path, keep_pngs: bool = False) -> None:
    """Makes `folder` and drops the last run's problems.txt (and its PNGs), so what lands there is this run's."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "problems.txt").unlink(missing_ok=True)
    if not keep_pngs:
        for png in folder.glob("*.png"):
            png.unlink()


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    command, (words, extra) = argv[0], split_extra(argv[1:])
    vm = vm_name()
    try:
        if command == "status":
            print(f"vm.sh: {status(vm)}")
            return 0
        if command not in COMMANDS:
            raise Usage(f"unknown command {command!r} ({', '.join(COMMANDS)}; --help)")
        config = companion.load()
        key = checkout_key(config.root)
        ctx = Context(config.root, config, vm, key, caches() / key)
        ctx.linked = linked_checkout(ctx)
        body = command_body(ctx, command, words, extra)
        ensure_ready(vm)
        run = owner(config.root, argv)
        with ExitStack() as locks:  # always in this order, so two runs never hold one each and wait for the other
            locks.enter_context(one_at_a_time(ctx.stage / "lock", "this checkout", run))
            if ctx.linked:
                linked = f"the linked Laching in {ctx.linked.guest}"
                locks.enter_context(one_at_a_time(ctx.linked.stage / "lock", linked, run))
            locks.enter_context(one_at_a_time(vm_lock(vm), f"the VM {vm!r}", run))
            sync(ctx)
            if body is None:
                return 0
            if command == "check-layout":
                folder = layout_folder(ctx)
                for language in words or ctx.config.languages:
                    clear_shots(folder / language)
                code = powershell(ctx, body).returncode
                print(f"vm.sh: the shots are in {folder}")
                return code
            if command != "screenshots":
                return powershell(ctx, body).returncode
            folder = shots_folder(words[0])
            clear_shots(folder, keep_pngs=True)
            code = powershell(ctx, body).returncode
            print(f"vm.sh: the shots are in {folder}")
            return code
    except Usage as mistake:
        print(f"vm.sh: {mistake}", file=sys.stderr)
        return 2
    except Unavailable as reason:
        print(f"vm.sh: {reason}", file=sys.stderr)
        return UNAVAILABLE
    except Busy as reason:
        print(f"vm.sh: {reason}", file=sys.stderr)
        return BUSY


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
