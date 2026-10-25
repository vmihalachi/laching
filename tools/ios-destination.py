#!/usr/bin/env python3
"""Prints an xcodebuild -destination for an iPhone simulator on the newest iOS this Xcode has, for the iOS tests:
simulator names change with every Xcode, so neither a workflow nor verify.sh names one.

    xcodebuild -scheme <mac.iosScheme> -destination "$(python3 laching/tools/ios-destination.py)" test
    python3 laching/tools/ios-destination.py --own "<name> verify"   # a simulator by that name (the same model, made
                                                                     # on first use) that nothing else boots

--own keeps the tests off the iPhone the Simulator panel and the screenshot runs share: a test host one of those left
running jams the next run. Needs Xcode (`xcrun simctl`).
"""

from __future__ import annotations

import argparse
import re
import sys

import companion
import sim


def version(runtime: dict) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", runtime.get("version", ""))]


def destination(own: str | None) -> str:
    found = sim.devices()
    for runtime in sorted(sim.runtimes(), key=version, reverse=True):
        here = [d for d in found if d["runtime"] == runtime["identifier"] and d.get("isAvailable", True)]
        phones = [d for d in here if d["name"].startswith("iPhone")]
        if not phones:
            continue
        udid = phones[0]["udid"]
        if own:
            mine = [d["udid"] for d in here if d["name"] == own]
            udid = mine[0] if mine else sim.simctl(
                "create", own, phones[0]["deviceTypeIdentifier"], runtime["identifier"]).strip()
        return f"platform=iOS Simulator,id={udid}"
    raise SystemExit("no iPhone simulator: add an iOS platform in Xcode → Settings → Components")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--own", metavar="NAME", help="a simulator of this name, made on first use")
    print(destination(parser.parse_args(argv).own))
    return 0


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main(sys.argv[1:]))
