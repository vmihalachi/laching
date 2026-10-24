#!/usr/bin/env python3
"""Portable Windows CI stages from companion.json: pins, content, exports, style, WinUI lint and .NET tests."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import companion
import companion_ci

TOOLS = Path(__file__).resolve().parent
STAGES = ("pins", "content", "strings", "format", "lint", "core", "app")


def commands(config: companion.Config, stage: str, export_dir: Path | None = None) -> list[companion_ci.Command]:
    root = config.root
    tool = lambda name, *args: companion_ci.tool(config, TOOLS, name, *args)
    if stage == "pins":
        return companion_ci.pins(config, TOOLS)
    if stage == "content":
        result = [tool("l10n.py", "check")]
        if config.phrases_file and config.phrases_file.is_file():
            result.append(tool("phrases.py", "check"))
        return result
    if stage == "strings":
        export_dir = export_dir or config.work_dir / "ci-windows-core-resw"
        return [tool("l10n.py", "export-windows", "--out", str(export_dir)),
                (root, ["diff", "-r", "--strip-trailing-cr", str(export_dir), str(config.require_path("windows.strings"))])]
    if stage == "format":
        return [(root, ["dotnet", "format", "whitespace", str(config.require_path("windows.dir")),
                        "--folder", "--verify-no-changes"])]
    if stage == "lint":
        return [tool("windows/check-winui.py")] if config.get("windows.app") else []
    if stage in ("core", "app"):
        key = "windows.coreTests" if stage == "core" else "windows.appTests"
        project = config.require_path(key, "windows.dir") if stage == "core" else config.path(key, "windows.dir")
        return [(root, ["dotnet", "test", str(project), "-c", "Release"])] if project else []
    raise ValueError(f"unknown stage: {stage}")


def run(config: companion.Config, stage: str) -> int:
    log_name = f"ci-windows-core-{stage}.log"
    if stage == "strings":
        # Each run owns its export folder, so removed languages can't survive a previous invocation.
        config.work_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ci-windows-core-resw-", dir=config.work_dir) as folder:
            return companion_ci.run(config, commands(config, stage, Path(folder)), log_name)
    return companion_ci.run(config, commands(config, stage), log_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    args = parser.parse_args(argv)
    config = companion_ci.load_config(parser, TOOLS)
    return run(config, args.stage)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
