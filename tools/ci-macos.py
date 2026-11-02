#!/usr/bin/env python3
"""The reusable macos-app.yml's checks (and core-linux.yml's `content` and `core`), using the checked-out app's pinned tools and companion.json.

Runs one named stage at a time. Every command is an argument list, never shell code interpolated from workflow
inputs or app names. Output is streamed to the job and saved under mac.dir/build/ci-<stage>.log for failure artifacts.
A stage the app has nothing for (no mac.iosScheme for `ios`, no conformance dir, writeFlag and corePackagePath for
`vectors`) runs no commands. Run locally, `vectors` leaves new vector files in the index (`git add --intent-to-add`).

    python3 laching/tools/ci-macos.py <stage>   # project, format, pins, content, vectors, core, test, ios, release, smoke
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import companion
import companion_ci

TOOLS = Path(__file__).resolve().parent
STAGES = ("project", "format", "pins", "content", "vectors", "core", "test", "ios", "release", "smoke")


def ios_destination(config: companion.Config) -> str:
    """A simulator of the app's own ("<name> ci", made on first use), off the iPhone the Simulator panel and the screenshot
    runs share on a self-hosted Mac. It needs Xcode, so it's asked only when the app has an iOS scheme."""
    spec = importlib.util.spec_from_file_location("ios_destination", TOOLS / "ios-destination.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.destination(f"{config.require('name')} ci")


def derived(config: companion.Config, suffix: str) -> Path:
    """A derived-data folder beside mac.derivedData, as verify.sh names the iOS one (dd.noindex → dd-ios.noindex)."""
    base = config.derived_data
    return base.with_name(f"{base.name.removesuffix('.noindex')}-{suffix}.noindex")


def release_app(config: companion.Config) -> Path:
    """The Release Mac app the `release` stage builds and `smoke` launches: mac.app's bundle."""
    return derived(config, "release") / "Build" / "Products" / "Release" / f"{config.require('mac.app')}.app"


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
    if stage == "vectors":
        vectors = config.path("conformance.dir")
        if vectors is None or not config.get("conformance.writeFlag") or not config.get("conformance.corePackagePath"):
            return []
        return [(root, [str(TOOLS / "conformance.sh")]),
                (root, ["git", "add", "--intent-to-add", "--", str(vectors)]),
                (root, ["git", "diff", "--exit-code", "--stat", "--", str(vectors)])]
    if stage == "core":
        core = config.path("mac.core", "mac.dir")
        return [(root, ["swift", "test", "--package-path", str(core)])] if core else []
    if stage == "test":
        return [(mac, ["xcodebuild", "-project", config.require("mac.project"), "-scheme", config.require("mac.scheme"),
                       "-destination", "platform=macOS", "-derivedDataPath", str(config.derived_data), "-quiet", *companion.xcode_cache_settings(), "test"])]
    if stage == "ios":
        scheme = config.get("mac.iosScheme")
        if not scheme:
            return []
        return [(mac, ["xcodebuild", "-project", config.require("mac.project"), "-scheme", scheme, "-destination", ios_destination(config),
                       "-derivedDataPath", str(derived(config, "ios")), "-quiet", *companion.xcode_cache_settings(), "test"])]
    if stage == "release":
        # What App Store review and customers get (hardened runtime, the Store bundle ID, Release-only strings), which
        # the tests never build: the Mac app signed ad hoc, the iOS app unsigned.
        project = config.require("mac.project")
        result = [(mac, ["xcodebuild", "-project", project, "-scheme", config.require("mac.scheme"), "-configuration", "Release",
                         "-destination", "platform=macOS", "-derivedDataPath", str(derived(config, "release")), "-quiet",
                         *companion.xcode_cache_settings(), "CODE_SIGN_STYLE=Manual", "CODE_SIGN_IDENTITY=-",
                         "DEVELOPMENT_TEAM=", "build"])]
        if scheme := config.get("mac.iosScheme"):
            result.append((mac, ["xcodebuild", "-project", project, "-scheme", scheme, "-configuration", "Release",
                                 "-destination", "generic/platform=iOS", "-derivedDataPath", str(derived(config, "release-ios")),
                                 "-quiet", *companion.xcode_cache_settings(), "CODE_SIGNING_ALLOWED=NO", "build"]))
        return result
    if stage == "smoke":
        return [(root, [str(TOOLS / "smoke-release.sh"), str(release_app(config))])] if config.get("mac.app") else []
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
