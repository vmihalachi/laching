#!/usr/bin/env python3
"""The reusable macos-app.yml's checks, using the checked-out app's pinned tools and companion.json.

Runs one named stage at a time. Every command is an argument list, never shell code interpolated from workflow
inputs or app names. Output is streamed to the job and saved under mac.dir/build/ci-<stage>.log for failure artifacts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import companion
import companion_ci

TOOLS = Path(__file__).resolve().parent
STAGES = ("project", "format", "pins", "content", "core", "test")


def commands(config: companion.Config, stage: str) -> list[tuple[Path, list[str]]]:
    """Commands with their working directories. Optional app features add only their own content checks."""
    root, mac = config.root, config.mac_dir
    tool = lambda name, *args: companion_ci.tool(config, TOOLS, name, *args)
    if stage == "project":
        spec = config.path("mac.projectSpec", "mac.dir")
        if spec is None:
            return []
        return [(mac, ["xcodegen", "generate", "--quiet", "--spec", str(spec)]),
                (root, ["git", "diff", "--exit-code", "--", str(config.path("mac.project", "mac.dir"))])]
    if stage == "format":
        roots = config.require("mac.format")
        if not roots:
            raise companion.ConfigError("mac.format: configure every owned Swift source root")
        paths = list(dict.fromkeys((mac / name).resolve() for name in roots))
        for path in paths:
            if not path.is_dir() and not (path.is_file() and path.suffix == ".swift"):
                raise companion.ConfigError(f"mac.format: Swift source root does not exist: {path}")
        return [(root, ["xcrun", "swift-format", "lint", "-r", "--strict", "--configuration",
                        str(mac / ".swift-format"), *(str(path) for path in paths)])]
    if stage == "pins":
        return companion_ci.pins(config, TOOLS)
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
    return companion_ci.run(config, commands(config, stage), f"ci-{stage}.log")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    args = parser.parse_args(argv)
    config = companion_ci.load_config(parser, TOOLS)
    return run(config, args.stage)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
