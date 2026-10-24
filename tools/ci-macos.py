#!/usr/bin/env python3
"""The reusable macos-app.yml's checks, using the checked-out app's pinned tools and companion.json.

Runs one named stage at a time. Every command is an argument list, never shell code interpolated from workflow
inputs or app names. Output is streamed to the job and saved under mac.dir/build/ci-<stage>.log for failure artifacts.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import companion

TOOLS = Path(__file__).resolve().parent
STAGES = ("project", "format", "pins", "content", "core", "test")


def commands(config: companion.Config, stage: str) -> list[tuple[Path, list[str]]]:
    """Commands with their working directories. Optional app features add only their own content checks."""
    root, mac = config.root, config.mac_dir
    python = sys.executable
    tool = lambda name, *args: (root, [python, str(TOOLS / name), *args])
    if stage == "project":
        spec = config.path("mac.projectSpec", "mac.dir")
        if spec is None:
            return []
        return [(mac, ["xcodegen", "generate", "--quiet", "--spec", str(spec)]),
                (root, ["git", "diff", "--exit-code", "--", str(config.path("mac.project", "mac.dir"))])]
    if stage == "format":
        candidates = [config.path(key, "mac.dir") for key in ("mac.app", "mac.tests")]
        candidates += [mac / name for name in ("Shared", "macOS", "iOS")]
        if core := config.path("mac.core", "mac.dir"):
            candidates += [core / "Sources", core / "Tests"]
        folders = list(dict.fromkeys(str(path) for path in candidates if path and path.is_dir()))
        if not folders:
            raise ValueError("mac: no Swift source folders configured")
        return [(root, ["xcrun", "swift-format", "lint", "-r", "--strict", "--configuration",
                        str(mac / ".swift-format"), *folders])]
    if stage == "pins":
        laching = config.require_path("laching.path")
        return [(root, ["git", "-C", str(laching), "fetch", "--quiet", "--depth=1", "origin",
                        "+refs/tags/*:refs/tags/*"]),
                tool("companion.py", "pins"), tool("companion.py", "conform")]
    if stage == "content":
        result = [tool("l10n.py", "check")]
        if config.phrases_file and config.phrases_file.is_file():
            result.append(tool("phrases.py", "check"))
        for section, name in (("sounds", "synth.py"), ("characters", "character.py"), ("voice", "voice.py")):
            if config.get(section):
                result.append(tool(name, "check" if section == "voice" else "--check"))
        return result
    if stage == "core":
        core = config.path("mac.core", "mac.dir")
        return [(root, ["swift", "test", "--package-path", str(core)])] if core else []
    if stage == "test":
        return [(mac, ["xcodebuild", "-project", config.require("mac.project"), "-scheme", config.require("mac.scheme"),
                       "-destination", "platform=macOS", "-derivedDataPath", str(config.derived_data), "-quiet", *companion.xcode_cache_settings(), "test"])]
    raise ValueError(f"unknown stage: {stage}")


def run(config: companion.Config, stage: str) -> int:
    """Run the stage's commands, stopping on failure and preserving the output for upload-artifact."""
    planned = commands(config, stage)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "COMPANION_ROOT": str(config.root)}
    with (config.work_dir / f"ci-{stage}.log").open("w", encoding="utf8") as log:
        for cwd, command in planned:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    args = parser.parse_args(argv)
    config = companion.load()
    # A workflow and the tools it invokes must belong to the same release. pins checks every reusable workflow ref;
    # this also refuses accidentally invoking another checkout's helper or a mismatched laching-path input.
    if config.require_path("laching.path").resolve() / "tools" != TOOLS:
        parser.error("run ci-macos.py from companion.json's laching.path/tools (the app's pinned submodule)")
    return run(config, args.stage)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
