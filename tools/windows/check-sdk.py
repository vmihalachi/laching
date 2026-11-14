#!/usr/bin/env python3
"""Fails, before anything builds, when this machine's .NET SDK can't build a companion's Windows projects.

GitHub's runners get their SDK from setup-dotnet. A self-hosted runner keeps its own, and one that is too old was found
minutes into a run, at the first build: NETSDK1045 when the SDK is older than the framework a project targets, the host
refusing every command when no installed SDK meets the app's windows/global.json. The Windows workflows run this first
on a self-hosted runner, and it runs wherever Python, dotnet and the app's checkout are:

    python3 laching/tools/windows/check-sdk.py

It asks `dotnet --version` in companion.json's windows.dir, where the host takes the SDK from the nearest global.json
(its rollForward included), and compares the answer with the newest `netN.M` among the TargetFramework(s) of the
*.csproj and *.props files there. Exit 1 says the fix in one line, an error annotation on GitHub Actions: "update the
runner's SDK to 11.0.100" for a pin that nothing installed meets, "to 11.0" for a target framework. Standard library
only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import companion  # noqa: E402

TARGETS = re.compile(r"<TargetFrameworks?(?:\s[^>]*)?>([^<]*)</TargetFrameworks?>")
FRAMEWORK = re.compile(r"net(\d+)\.(\d+)(?:-[\w.]+)?")  # net11.0, net11.0-windows10.0.22000.0; never netstandard2.0, net48
VERSION = re.compile(r"(\d+)\.(\d+)\.\d+\S*")


def dotnet(folder: Path, *args: str) -> tuple[int | None, str]:
    """dotnet's exit code and what it printed, run in `folder`; (None, why) when it can't start."""
    try:
        done = subprocess.run(["dotnet", *args], cwd=folder, capture_output=True, encoding="utf8", errors="replace")
    except OSError as error:
        return None, error.strerror or str(error)
    return done.returncode, done.stdout + done.stderr


def pin(folder: Path) -> tuple[Path, dict] | None:
    """The global.json the host reads in `folder` (the nearest one, the folders above included) and its `sdk`."""
    for candidate in (folder, *folder.parents):
        path = candidate / "global.json"
        if path.is_file():
            try:
                sdk = json.loads(path.read_text("utf-8-sig")).get("sdk")
            except (ValueError, AttributeError):  # comments, which the host reads and json doesn't
                sdk = None
            return path, sdk if isinstance(sdk, dict) else {}
    return None


def newest_target(folder: Path) -> tuple[tuple[int, int], Path] | None:
    """The newest .NET a project under `folder` targets, as (major, minor), and the first file that names it."""
    found = None
    for base, folders, files in os.walk(folder):
        folders[:] = sorted(f for f in folders if f not in ("bin", "obj") and not f.startswith("."))
        for name in sorted(files):
            if not name.endswith((".csproj", ".props")):
                continue
            text = re.sub(r"<!--.*?-->", "", (Path(base) / name).read_text("utf-8-sig", errors="replace"), flags=re.S)
            for frameworks in TARGETS.findall(text):
                for framework in frameworks.split(";"):
                    match = FRAMEWORK.fullmatch(framework.strip())
                    version = (int(match.group(1)), int(match.group(2))) if match else None
                    if version and (found is None or version > found[0]):
                        found = version, Path(base) / name
    return found


def check(config: companion.Config, run=dotnet) -> tuple[bool, str]:
    """Whether the SDK that dotnet picks in windows.dir builds the projects there, and the line that says so."""
    folder = config.require_path("windows.dir")
    if not folder.is_dir():
        return False, f"{companion.FILE}'s windows.dir is {config.get('windows.dir')}, which isn't a folder."
    where = lambda path: companion.rel(path, config.root)
    pinned, target = pin(folder), newest_target(folder)
    wanted = pinned[1].get("version") if pinned else None
    framework = f"{target[0][0]}.{target[0][1]}" if target else None
    fix = f"update the runner's SDK to {wanted or framework}" if wanted or framework else "install a .NET SDK on the runner"
    code, said = run(folder, "--version")
    if code is None:
        return False, f"dotnet doesn't start here ({said}): {fix}."
    if code:
        installed = re.findall(r"^(\d\S*) \[", run(folder, "--list-sdks")[1], re.M)
        have = f"this machine has {', '.join(installed)}" if installed else "this machine has none"
        # The host's first line that isn't an installed SDK ("11.0.100 [/usr/share/dotnet/sdk]").
        reason = next((line.strip() for line in said.splitlines() if line.strip() and not re.match(r"\s*\d\S* \[", line)),
                      f"exit code {code}")
        if wanted:
            policy = f", rollForward {pinned[1]['rollForward']}" if pinned[1].get("rollForward") else ""
            return False, f"No installed .NET SDK meets {where(pinned[0])} ({wanted}{policy}); {have}: {fix}. dotnet says: {reason}"
        return False, f"`dotnet --version` fails in {where(folder)} ({reason}); {have}: {fix}."
    sdk = next(filter(None, (VERSION.fullmatch(line.strip()) for line in reversed(said.splitlines()))), None)
    if not sdk:
        return True, f"ok: `dotnet --version` gave no version to compare ({said.strip()[:80]})"
    if target and (int(sdk.group(1)), int(sdk.group(2))) < target[0]:
        picked = f", the one {where(pinned[0])} picks," if pinned else ""
        both = " and the pin with it" if pinned else ""
        return False, (f"The .NET SDK here is {sdk.group()}{picked} and {where(target[1])} targets net{framework} "
                       f"(the build stops at NETSDK1045): update the runner's SDK to {framework}{both}.")
    builds = f" builds net{framework}" if target else ""
    return True, f"ok: the .NET SDK {sdk.group()}{builds}" + (f" ({where(pinned[0])} picks it)" if pinned else "")


def main() -> int:
    try:
        fine, line = check(companion.load())
    except companion.ConfigError as error:  # an app without a windows section
        sys.exit(f"check-sdk: {error}")
    if fine:
        print(line)
    elif os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error::{line}")
    else:
        print(f"error: {line}", file=sys.stderr)
    return 0 if fine else 1


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
