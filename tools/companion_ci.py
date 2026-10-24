"""Shared command execution for companion CI drivers, with pinned tools and streamed failure logs."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import companion

Command = tuple[Path, list[str]]


def tool(config: companion.Config, tools: Path, name: str, *args: str) -> Command:
    return config.root, [sys.executable, str(tools / name), *args]


def pins(config: companion.Config, tools: Path) -> list[Command]:
    return [(config.root, ["git", "-C", str(config.require_path("laching.path")), "fetch", "--quiet", "--depth=1",
                           "origin", "+refs/tags/*:refs/tags/*"]),
            tool(config, tools, "companion.py", "pins"), tool(config, tools, "companion.py", "conform")]


def load_config(parser: argparse.ArgumentParser, tools: Path) -> companion.Config:
    """Refuse a workflow path input that selects tools outside the app's pinned submodule."""
    config = companion.load()
    if config.require_path("laching.path") / "tools" != tools:
        parser.error("run this driver from companion.json's laching.path/tools (the app's pinned submodule)")
    return config


def run(config: companion.Config, commands: list[Command], log_name: str) -> int:
    """Run argv lists in order, preserving output and stopping at the first failed command."""
    config.work_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "COMPANION_ROOT": str(config.root)}
    with (config.work_dir / log_name).open("w", encoding="utf8") as log:
        for cwd, command in commands:
            print(f"::group::{Path(command[0]).name} {' '.join(command[1:])}", flush=True)
            try:
                with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, encoding="utf8", errors="replace") as process:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                        log.flush()
                    code = process.wait()
            finally:
                print("::endgroup::", flush=True)
            if code:
                return code
    return 0
