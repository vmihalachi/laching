#!/usr/bin/env python3
"""Tests for tools/companion.py: finding the root, the schema, the lookups and the CLI."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import companion  # noqa: E402
from support import FIXTURE_APP, HERE, REPO, fixture_app  # noqa: E402

PAGLIACCI = REPO.parent / "pagliacci" / "companion.json"
MYBACKHURTS = REPO.parent / "mybackhurts" / "companion.json"


class SchemaTests(unittest.TestCase):
    def check(self, path: Path) -> list[str]:
        data = json.loads(path.read_text("utf8"))
        return companion.validate(data) + companion.consistency(data)

    def test_the_fixture_app_and_sunnysays_draft_validate(self):
        self.assertEqual(self.check(FIXTURE_APP / "companion.json"), [])
        self.assertEqual(self.check(HERE / "sunnysays.companion.json"), [])

    @unittest.skipUnless(PAGLIACCI.exists(), "../pagliacci isn't checked out next to laching")
    def test_pagliacci_validates(self):
        self.assertEqual(self.check(PAGLIACCI), [])

    @unittest.skipUnless(MYBACKHURTS.exists(), "../mybackhurts isn't checked out next to laching")
    def test_mybackhurts_validates(self):
        self.assertEqual(self.check(MYBACKHURTS), [])

    def test_an_app_without_a_core_package(self):
        data = json.loads((FIXTURE_APP / "companion.json").read_text("utf8"))
        del data["mac"]["core"], data["mac"]["coreModule"], data["conformance"]
        self.assertEqual(companion.validate(data), [])
        names = companion.derived(companion.Config(data, FIXTURE_APP))
        self.assertEqual((names["MAC_CORE"], names["RE_PURE"], names["RE_COREPKG"]), ("", "^$a", "^$a"))

    def test_problems_are_named(self):
        data = json.loads((FIXTURE_APP / "companion.json").read_text("utf8"))
        data["mac"]["scheme"] = 5
        data["characters"] = {"design": "d", "mode": "pixels"}
        data["nonsense"] = 1
        del data["l10n"]
        data["voice"]["defaultVoice"] = "mime"
        problems = companion.validate(data) + companion.consistency(data)
        joined = "\n".join(problems)
        for needle in ("scheme", "'pixels' is not one of", "unknown key nonsense", "missing l10n", "defaultVoice"):
            self.assertIn(needle, joined)

    def test_nullable_sections(self):
        data = json.loads((FIXTURE_APP / "companion.json").read_text("utf8"))
        self.assertIsNone(data["fonts"])
        data["fonts"] = {"out": "x"}
        self.assertIn("fonts: missing family", "\n".join(companion.validate(data)))


class LookupTests(unittest.TestCase):
    def test_root_from_the_environment_and_from_below(self):
        with fixture_app() as root:
            self.assertEqual(companion.find_root(), root)
            os.environ.pop("COMPANION_ROOT")
            self.assertEqual(companion.find_root(root / "macos" / "Fixture"), root)
            os.environ["COMPANION_ROOT"] = str(root)

    def test_no_root(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = os.environ.pop("COMPANION_ROOT", None)
            try:
                with self.assertRaises(companion.ConfigError):
                    companion.find_root(Path(temp))
            finally:
                if previous:
                    os.environ["COMPANION_ROOT"] = previous

    def test_get_and_path(self):
        with fixture_app() as root:
            config = companion.load()
            self.assertEqual(config.get("mac.scheme"), "Fixture")
            self.assertEqual(config.get("voice.voices"), ["clown"])
            self.assertIsNone(config.get("fonts"))
            self.assertIsNone(config.get("l10n.plist"))
            self.assertEqual(config.get("mac.nothing", "x"), "x")
            self.assertEqual(config.path("l10n.catalog"), root / "macos/Fixture/Resources/Localizable.xcstrings")
            self.assertEqual(config.path("app", base="mac.dir"), None)
            self.assertEqual(config.path("mac.app", base="mac.dir"), root / "macos" / "Fixture")
            self.assertEqual(config.phrases_file, root / "conformance" / "phrases.json")
            self.assertEqual(config.languages, ["en", "it", "de"])
            with self.assertRaises(companion.ConfigError):
                config.require("fonts.out")

    def test_cli(self):
        with fixture_app() as root:
            for argv, expected in (
                (["get", "mac.scheme"], "Fixture"),
                (["get", "languages.all"], '["en", "it", "de"]'),
                (["path", "voice.dir"], str(root / "voices")),
                (["root"], str(root)),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(companion.main(argv), 0)
                self.assertEqual(out.getvalue().strip(), expected)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(companion.main(["get", "fonts"]), 1)
                self.assertEqual(companion.main(["validate"]), 0)

    def test_env_paths_and_patterns(self):
        with fixture_app():
            names = companion.derived(companion.load())
            self.assertEqual(names["MAC_CORE"], "macos/FixtureCore")
            self.assertEqual(names["WINDOWS_STRINGS"], "windows/Fixture.App/Strings")
            self.assertEqual(names["RE_LANDING"], "^$a", "no landing page: a pattern nothing matches")
            self.assertRegex("macos/FixtureTests/AppTests.swift", names["RE_SWIFT"])
            self.assertRegex("macos/project.yml", names["RE_SWIFT"])
            self.assertNotRegex("macos/FixtureCore/Sources/A.swift", names["RE_SWIFT"])
            self.assertRegex("windows/Fixture.Core.Tests/A.cs", names["RE_WINDOWS_CORE"])
            self.assertNotRegex("windows/FixtureXCore/A.cs", names["RE_WINDOWS_CORE"], "the dot is literal")
            self.assertEqual(names["WINDOWS_APP_TESTS"], "Fixture.App.Tests")
            self.assertEqual(names["WINDOWS_ASSETS"], "windows/Fixture.App/Assets")
            for path in ("windows/Fixture.App/App/AppModel.cs", "windows/Fixture.App.Tests/AppModelTests.cs"):
                self.assertRegex(path, names["RE_WINDOWS_APP_TESTS"])
            self.assertNotRegex("windows/Fixture.Core/A.cs", names["RE_WINDOWS_APP_TESTS"])
            for path in ("macos/Fixture/Resources/AppIcon.png", "macos/Fixture/Resources/AppIcon-Dev.png",
                         "windows/Fixture.App/Assets/StoreLogo.scale-100.png"):
                self.assertRegex(path, names["RE_WINDOWS_ASSETS"])
            self.assertNotRegex("macos/Fixture/Resources/AppIcon.png.bak", names["RE_WINDOWS_ASSETS"])
            for path in ("macos/scripts/sounds.py", "macos/Fixture/Resources/Sounds/blip.wav", "laching"):
                self.assertRegex(path, names["RE_SOUNDS"])
            self.assertNotRegex("macos/Fixture/Resources/Sounds/README.md", names["RE_SOUNDS"])
            self.assertEqual(names["RE_CHARACTERS"], "^$a", "no characters section")

    def test_character_paths_and_patterns(self):
        with fixture_app() as root:
            manifest = root / "companion.json"
            data = json.loads(manifest.read_text("utf8"))
            data["characters"] = {"design": "design/characters", "swiftOut": "macos/FixtureCore/Sources/FixtureCore/Art",
                                  "assets": "macos/Fixture/Characters", "paletteFile": "design/palette.json"}
            manifest.write_text(json.dumps(data), "utf8")
            names = companion.derived(companion.load())
            self.assertEqual(names["CHARACTERS_ASSETS"], "", "a vector app's assets aren't character.py's")
            for path in ("design/characters/tiny/body.svg", "design/palette.json", "conformance/characters/tiny.json",
                         "macos/FixtureCore/Sources/FixtureCore/Art/TinyArt.swift", "laching"):
                self.assertRegex(path, names["RE_CHARACTERS"])
            for path in ("macos/FixtureCore/Sources/FixtureCore/Art/Pose.swift", "conformance/phrases.json",
                         "macos/Fixture/Characters/tiny.face.imageset/Contents.json"):
                self.assertNotRegex(path, names["RE_CHARACTERS"])
            data["characters"]["mode"] = "raster"
            manifest.write_text(json.dumps(data), "utf8")
            names = companion.derived(companion.load())
            self.assertEqual(names["CHARACTERS_ASSETS"], "macos/Fixture/Characters")
            self.assertRegex("macos/Fixture/Characters/tiny.face.imageset/Contents.json", names["RE_CHARACTERS"])

    def test_android_names_and_patterns(self):
        with fixture_app() as root:
            names = companion.derived(companion.load())
            self.assertEqual(names["ANDROID_DIR"], "", "no Android app")
            self.assertEqual(names["RE_ANDROID_CORE"], "^$a")
            manifest = root / "companion.json"
            data = json.loads(manifest.read_text("utf8"))
            data["android"] = {"dir": "android", "strings": "android/app/src/main/res"}
            data["hooks"]["androidUI"] = "^android/app/src/main/kotlin/app/fixture/ui/"
            self.assertEqual(companion.validate(data), [])
            manifest.write_text(json.dumps(data), "utf8")
            names = companion.derived(companion.load())
            self.assertEqual(names["ANDROID_DIR"], "android")
            self.assertEqual((names["ANDROID_CORE"], names["ANDROID_APP"]), ("core", "app"), "the defaults")
            self.assertEqual(names["ANDROID_APP_ID"], "app.fixture", "bundleId by default")
            self.assertEqual(names["ANDROID_DEV_APP_ID"], "app.fixture.dev")
            self.assertEqual(names["ANDROID_STRINGS"], "android/app/src/main/res")
            for file in ("android/core/src/main/kotlin/A.kt", "conformance/scheduler/a.json"):
                self.assertRegex(file, names["RE_ANDROID_CORE"])
            self.assertNotRegex("android/app/src/main/kotlin/A.kt", names["RE_ANDROID_CORE"])
            self.assertRegex("android/app/src/main/kotlin/A.kt", names["RE_ANDROID_APP"])
            for file in ("android/build.gradle.kts", "android/core/src/main/kotlin/A.kt"):
                self.assertRegex(file, names["RE_KOTLIN"])
            self.assertNotRegex("android/gradle.properties", names["RE_KOTLIN"])

    def test_pins_agree_and_disagree(self):
        with fixture_app() as root:
            config = companion.load()
            found = dict(companion.pins(config))
            self.assertEqual(found["macos/FixtureCore/Package.swift"], "0.5.0")
            self.assertEqual(found["macos/project.yml"], "0.5.0")
            self.assertEqual(found["windows/Fixture.Core/Fixture.Core.csproj Laching.Companion"], "0.5.0")
            self.assertTrue(companion.pins_agree(list(found.items())))
            csproj = root / "windows/Fixture.Core/Fixture.Core.csproj"
            csproj.write_text(csproj.read_text("utf8").replace('Laching.Core" Version="0.5.0"', 'Laching.Core" Version="0.4.0"'), "utf8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(companion.main(["pins"]), 1)
            self.assertIn("must be the same everywhere", out.getvalue())
            self.assertIn("0.4.0", out.getvalue())
            spec = root / "macos/project.yml"
            spec.write_text(spec.read_text("utf8").replace("exactVersion: 0.5.0", "path: ../laching"), "utf8")
            self.assertIn(("macos/project.yml", "(not pinned to a version)"), companion.pins(config))

    def test_an_invalid_file_stops_the_tools(self):
        with fixture_app() as root:
            data = json.loads((root / "companion.json").read_text("utf8"))
            del data["mac"]
            (root / "companion.json").write_text(json.dumps(data), "utf8")
            with self.assertRaises(SystemExit) as stop:
                companion.load()
            self.assertIn("missing mac", str(stop.exception.code))


if __name__ == "__main__":
    unittest.main()
