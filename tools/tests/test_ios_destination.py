"""ios-destination.py: the iOS tests' simulator, on the newest runtime, or one of their own."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("ios_destination", TOOLS / "ios-destination.py")
ios_destination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ios_destination)

RUNTIMES = [{"identifier": "iOS-17-5", "version": "17.5"}, {"identifier": "iOS-26-1", "version": "26.1"},
            {"identifier": "iOS-26-0", "version": "26.0"}]
PHONE = "com.apple.CoreSimulator.SimDeviceType.iPhone-17"


def device(name: str, udid: str, runtime: str, available: bool = True) -> dict:
    return {"name": name, "udid": udid, "runtime": runtime, "isAvailable": available, "deviceTypeIdentifier": PHONE}


class IosDestinationTests(unittest.TestCase):
    def destination(self, devices: list[dict], own: str | None = None) -> tuple[str, mock.Mock]:
        with mock.patch.object(ios_destination.sim, "runtimes", return_value=RUNTIMES), \
                mock.patch.object(ios_destination.sim, "devices", return_value=devices), \
                mock.patch.object(ios_destination.sim, "simctl", return_value="NEW-UDID\n") as simctl:
            return ios_destination.destination(own), simctl

    def test_an_iphone_on_the_newest_runtime_that_has_one(self):
        found = [device("iPhone 15", "OLD", "iOS-17-5"), device("iPad Pro", "PAD", "iOS-26-1"),
                 device("iPhone 17", "GONE", "iOS-26-1", available=False), device("iPhone 17", "NEW", "iOS-26-0")]
        self.assertEqual(self.destination(found)[0], "platform=iOS Simulator,id=NEW", "26.1 has no iPhone that runs")

    def test_own_reuses_its_simulator_or_makes_it(self):
        found = [device("iPhone 17", "SHARED", "iOS-26-1"), device("app verify", "MINE", "iOS-26-1")]
        self.assertEqual(self.destination(found, "app verify")[0], "platform=iOS Simulator,id=MINE")
        made, simctl = self.destination(found[:1], "app verify")
        self.assertEqual(made, "platform=iOS Simulator,id=NEW-UDID")
        simctl.assert_called_once_with("create", "app verify", PHONE, "iOS-26-1")

    def test_no_iphone(self):
        with self.assertRaisesRegex(SystemExit, "no iPhone simulator"):
            self.destination([device("iPad Pro", "PAD", "iOS-26-1")])


if __name__ == "__main__":
    unittest.main()
