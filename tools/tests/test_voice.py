#!/usr/bin/env python3
"""Tests for tools/voice.py's offline parts over the fixture app: clip ids (a joke's parts, language scoping), the
pending report, the check, render's prune, the friends' site, the review page, the render hash and the mouth envelope.
Nothing here talks to ElevenLabs."""

from __future__ import annotations

import argparse
import io
import json
import ntpath
import subprocess
import sys
import unittest
import wave
from array import array
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import companion  # noqa: E402
import voice  # noqa: E402
from support import FixtureAppTestCase  # noqa: E402

TEXTS = {
    "hello": "Ciao.",
    "joke.1": "Perché il clown si è seduto?",
    "joke.2": "Aveva i piedi stanchi.",
    "ciao": "Ciao a tutti!",
    "ui-test-voice": "Ciao. Ecco la mia voce.",
}


class VoiceTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        voice.configure(companion.load())

    def check(self) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return voice.cmd_check(None)

    def add_clip(self, clip: str, text: str | None = None, lang: str = "it", kind: str = "clown",
                 render_hash: str = "r") -> None:
        path = voice.clip_path(lang, kind, clip)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"aac")
        manifest = voice.load_manifest()
        entry = manifest["languages"].setdefault(lang, {"status": "testing", "default": "clown", "voices": {}})
        entry["voices"].setdefault(kind, {"clips": {}})["clips"][clip] = {
            "textHash": voice.text_hash(text or TEXTS[clip]), "renderHash": render_hash, "duration": 1.0,
            "mouth": "0110"}
        voice.dump_json(voice.MANIFEST, manifest)

    def add_every_clip(self) -> None:
        """Every Italian line recorded from its current text and render settings: nothing left to render."""
        for clip, text, _, rhash, _ in voice.plan_render("it", "clown", voice.load_config(), None, False):
            self.add_clip(clip, text, render_hash=rhash)

    def prune(self, **flags) -> str:
        """`voice.py render --lang it --prune` with `flags` (`dry_run`, `yes`): what it printed."""
        args = argparse.Namespace(lang="it", voice=None, only=None, force=False, prune=True,
                                  **{"dry_run": False, "yes": False, **flags})
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(voice.cmd_render(args), 0)
        return out.getvalue()

    def test_names_come_from_companion_json(self):
        self.assertEqual(voice.KINDS, ("clown",))
        self.assertEqual(voice.KEYCHAIN_SERVICE, "fixture-elevenlabs")
        self.assertEqual(voice.VOICE, self.root / "voices" / "Voice")
        self.assertEqual(voice.REVIEW_TITLE, "Fixy's voice")
        self.assertIn("© Fixture", voice.SITE_NOTICE)

    def test_a_joke_has_a_clip_per_part_and_scoped_phrases_stay_in_their_languages(self):
        config = voice.load_config()
        self.assertEqual(list(voice.clip_keys(config, "it")), ["hello", "joke.1", "joke.2", "ciao", "ui-test-voice"])
        self.assertEqual(voice.clip_keys(config, "it")["joke.2"], "phrase.joke.2")
        self.assertNotIn("ciao", voice.clip_keys(config, "de"), "the Italian-only line isn't recorded in German")
        self.assertIn("ciao", voice.clip_keys(config), "but it's a known clip, never an orphan")

    def test_pending_marks_missing_stale_and_voiced(self):
        self.add_clip("hello")
        self.add_clip("joke.1", "Perché il clown?")  # recorded from older text
        state = voice.pending("it", "clown", voice.load_config(), voice.load_manifest())
        self.assertEqual(state, {"hello": None, "joke.1": "stale", "joke.2": "missing", "ciao": "missing", "ui-test-voice": "missing"})

    def test_a_joke_is_voiced_only_with_every_part(self):
        for clip in ("hello", "ciao", "joke.1"):
            self.add_clip(clip)
        male = voice.report(voice.load_config())["voiced"]["it"]["voices"]["clown"]
        self.assertIn("tag buttonPress", male["gaps"], "only the joke has buttonPress, and its punchline is missing")
        self.assertIn("flavor b", male["gaps"])
        self.add_clip("joke.2")
        male = voice.report(voice.load_config())["voiced"]["it"]["voices"]["clown"]
        self.assertEqual(male["gaps"], [])
        self.assertEqual(male["requiredPending"], ["ui-test-voice"])

    def test_check_passes_with_pending_lines_while_testing(self):
        self.add_clip("hello")
        self.assertEqual(self.check(), 0)

    def test_check_fails_on_a_required_clip_when_shipping(self):
        config = voice.load_config()
        config["languages"]["it"]["status"] = "shipping"
        voice.dump_json(voice.CONFIG, config)
        self.add_clip("hello")
        manifest = voice.load_manifest()
        manifest["languages"]["it"]["status"] = "shipping"
        voice.dump_json(voice.MANIFEST, manifest)
        self.assertEqual(self.check(), 1)
        self.add_clip("ui-test-voice")
        self.assertEqual(self.check(), 0)

    def test_check_fails_on_orphans_and_stray_files(self):
        self.add_clip("retired", "Addio.")
        self.assertEqual(self.check(), 1)
        manifest = voice.load_manifest()
        del manifest["languages"]["it"]["voices"]["clown"]["clips"]["retired"]
        voice.dump_json(voice.MANIFEST, manifest)
        self.assertEqual(self.check(), 1, "the file is still on disk, outside the manifest")
        voice.clip_path("it", "clown", "retired").unlink()
        self.assertEqual(self.check(), 0)

    def test_a_dry_run_prune_only_says_what_it_would_remove(self):
        self.add_every_clip()
        self.add_clip("retired", "Addio.")
        before = voice.MANIFEST.read_bytes()
        out = self.prune(dry_run=True)
        self.assertIn("would prune retired", out)
        self.assertNotIn("pruned retired", out)
        self.assertTrue(voice.clip_path("it", "clown", "retired").exists(), "the file stays on disk")
        self.assertEqual(voice.MANIFEST.read_bytes(), before, "and in the manifest")

    def test_prune_removes_an_orphan_from_disk_and_manifest(self):
        self.add_every_clip()
        self.add_clip("retired", "Addio.")
        self.assertEqual(self.check(), 1)
        self.assertIn("pruned retired", self.prune())
        self.assertFalse(voice.clip_path("it", "clown", "retired").exists())
        self.assertNotIn("retired", voice.manifest_clips(voice.load_manifest(), "it", "clown"))
        self.assertEqual(self.check(), 0)

    def test_a_prune_that_stops_before_recording_leaves_files_and_manifest_agreeing(self):
        self.add_clip("hello", render_hash="old")  # so there are lines to render
        self.add_clip("retired", "Addio.")
        before = voice.MANIFEST.read_bytes()
        with self.assertRaises(SystemExit):
            self.prune()  # no --yes
        self.assertTrue(voice.clip_path("it", "clown", "retired").exists(), "a refused run prunes nothing")
        self.assertEqual(voice.MANIFEST.read_bytes(), before)
        with mock.patch.object(voice, "tts", side_effect=SystemExit(2)), self.assertRaises(SystemExit):
            self.prune(yes=True)  # ElevenLabs fails on the first line
        self.assertFalse(voice.clip_path("it", "clown", "retired").exists())
        self.assertNotIn("retired", voice.manifest_clips(voice.load_manifest(), "it", "clown"))
        self.assertEqual(self.check(), 0)

    def test_site_follows_the_clips(self):
        self.add_clip("hello")
        site = voice.VOICE.parent
        (site / ".git").write_text("gitdir: elsewhere")
        self.assertEqual(self.check(), 1, "the voices repository without its pages")
        voice.sync_site(voice.load_config(), voice.load_manifest())
        self.assertEqual(self.check(), 0)
        page = (site / "review" / "it.html").read_text("utf8")
        self.assertIn('"../Voice/it/clown/hello.m4a?v=r"', page)
        self.assertIn("<title>Fixy's voice review</title>", (site / "index.html").read_text("utf8"))
        self.assertIn("Copyright (c) Fixture", (site / "LICENSE").read_text("utf8"))
        self.assertIn("`laching/tools/voice.py` in fixture", (site / "README.md").read_text("utf8"))
        self.assertIn("The recorded lines of Fixy, in Fixture, made with ElevenLabs:", (site / "README.md").read_text("utf8"))

    def test_clip_urls_use_forward_slashes_on_windows(self):
        # os.path is ntpath on Windows: the relative path comes back with backslashes, which a URL can't use.
        with mock.patch.object(voice.os, "path", ntpath):
            url = voice.url_path(Path("/app/voices/Voice/it/clown/hello.m4a"), Path("/app/voices/review"))
        self.assertEqual(url, "../Voice/it/clown/hello.m4a")

    def test_site_for_an_app_named_after_its_character(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["character"] = data["displayName"]
        data["voice"]["copyrightHolder"] = "2026 Jane Doe"
        path.write_text(json.dumps(data), "utf8")
        voice.configure(companion.load())
        voice.sync_site(voice.load_config(), voice.load_manifest())
        site = voice.VOICE.parent
        self.assertIn("The recorded lines of Fixture, made with ElevenLabs:", (site / "README.md").read_text("utf8"))
        self.assertTrue((site / "LICENSE").read_text("utf8").startswith("Copyright (c) 2026 Jane Doe. All rights reserved."))

    def test_review_page_lists_a_jokes_parts_under_it(self):
        self.add_clip("joke.1")
        self.add_clip("joke.2", "Old punchline.")
        page = voice.WORK / "review" / "it.html"
        html_text, count = voice.review_page("it", ["clown"], page, False, voice.load_config(), voice.load_manifest())
        data = json.loads(html_text.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        rows = {r["id"]: r for r in data["rows"]}
        self.assertEqual(count, 1)
        self.assertEqual([r["id"] for r in data["rows"]], ["hello", "joke.1", "joke.2", "ciao", "ui-test-voice"])
        self.assertEqual((rows["joke.1"]["joke"], rows["joke.1"]["part"], rows["joke.1"]["parts"]), ("joke", 1, 2))
        self.assertEqual(rows["joke.2"]["clips"], {}, "the old audio says the old text")
        self.assertEqual(rows["hello"]["joke"], "")
        self.assertEqual(data["title"], "Fixy's voice")
        self.assertEqual(data["flavors"], ["a", "b"])
        self.assertEqual(data["languages"], [{"code": "it", "name": "Italian", "recorded": True}])
        self.assertNotIn("Sunny", html_text)

    def test_review_pages_take_the_apps_colors(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["voice"]["reviewColors"] = {"light": {"accent": "#B23A1F"}, "dark": {"accent": "#F2B23A"}}
        path.write_text(json.dumps(data), "utf8")
        voice.configure(companion.load())
        page = voice.WORK / "review" / "it.html"
        html_text, _ = voice.review_page("it", ["clown"], page, False, voice.load_config(), voice.load_manifest())
        self.assertIn(":root{--accent:#B23A1F;}@media (prefers-color-scheme: dark){:root{--accent:#F2B23A;}}", html_text)
        self.assertNotIn("/*COLORS*/", html_text)
        data["voice"]["reviewColors"] = {"light": {"sun": "#B23A1F"}}
        path.write_text(json.dumps(data), "utf8")
        voice.configure(companion.load())
        with self.assertRaises(SystemExit):
            voice.review_colors()

    def test_site_takes_reactions_an_icon_and_link_previews(self):
        self.add_clip("hello")
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["voice"].update({"reactions": ["Funny", "Meh"], "siteIcon": "icon.png", "repo": "someone/fixture-voices"})
        path.write_text(json.dumps(data), "utf8")
        (self.root / "icon.png").write_bytes(b"\x89PNG fixture")
        voice.configure(companion.load())
        voice.sync_site(voice.load_config(), voice.load_manifest())
        site = voice.VOICE.parent
        page = (site / "review" / "it.html").read_text("utf8")
        shown = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual((shown["reactions"], shown["icon"], shown["source"]), (["Funny", "Meh"], "../icon.png", "en"))
        self.assertEqual((site / "icon.png").read_bytes(), b"\x89PNG fixture")
        self.assertIn('<link rel="icon" type="image/png" href="../icon.png">', page)
        self.assertIn('<meta property="og:url" content="https://someone.github.io/fixture-voices/review/it.html">', page)
        index = (site / "index.html").read_text("utf8")
        self.assertIn('<meta property="og:image" content="https://someone.github.io/fixture-voices/icon.png">', index)
        self.assertIn('data-lang="it" data-lines="1"', index)
        self.assertEqual(voice.stale_site(voice.load_config(), voice.load_manifest()), [])
        owner, _ = voice.review_page("it", ["clown"], voice.WORK / "review" / "it.html", False, voice.load_config(),
                                     voice.load_manifest())
        self.assertNotIn("og:", owner, "the owner's page is local: no previews")
        self.assertNotIn("<!--HEAD-->", owner)
        data["voice"]["reactions"] = ["Funny", " "]
        path.write_text(json.dumps(data), "utf8")
        voice.configure(companion.load())
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            voice.review_reactions()

    def test_a_site_page_in_a_translated_language_speaks_it(self):
        self.add_clip("hello")
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["voice"].update({"reactions": ["Funny", "Meh"], "siteText": {"it": {
            "reviewTitle": "La voce di Fixy", "voices": {"clown": "Pagliaccio"}, "flavors": {"a": "alfa"},
            "reactions": ["Divertente", "Così così"]}}})
        path.write_text(json.dumps(data), "utf8")
        voice.configure(companion.load())
        voice.sync_site(voice.load_config(), voice.load_manifest())
        site = voice.VOICE.parent
        page = (site / "review" / "it.html").read_text("utf8")
        shown = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        self.assertIn('<html lang="it">', page)
        self.assertIn("<title>La voce di Fixy · Italiano</title>", page)
        self.assertEqual(shown["ui"]["Sounds off"], "Suona male")
        self.assertEqual((shown["title"], shown["languageLabel"], shown["voices"][0]["label"]),
                         ("La voce di Fixy", "Italiano", "Pagliaccio"))
        self.assertEqual(shown["flavorLabels"], {"a": "alfa", "b": "b"})
        self.assertEqual((shown["reactions"], shown["reactionLabels"]), (["Funny", "Meh"], ["Divertente", "Così così"]),
                         "the notes sent back stay in English")
        self.assertTrue(shown["notice"].startswith("Condiviso con gli amici che aiutano a rivedere la voce di Fixy."))
        self.assertIn("Ascolta le frasi di Fixy in italiano", page)
        index = (site / "index.html").read_text("utf8")
        self.assertIn("<title>Fixy's voice review</title>", index, "English until the browser asks for Italian")
        self.assertIn('"title": "La voce di Fixy: la revisione"', index)
        self.assertEqual((voice.say("it", "{n} lines", n=1), voice.say("it", "{n} lines", n=3)), ("1 frase", "3 frasi"))
        self.assertEqual(voice.say("de", "{n} lines", n=1, one="{n} line"), "1 line", "no translation: English")
        owner, _ = voice.review_page("it", ["clown"], voice.WORK / "review" / "it.html", False, voice.load_config(),
                                     voice.load_manifest())
        mine = json.loads(owner.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        self.assertIn('<html lang="en">', owner)
        self.assertNotIn("ui", mine, "the owner's page stays English")
        self.assertEqual(mine["title"], "Fixy's voice")

    def test_publish_pushes_to_origins_default_branch(self):
        remote, site = self.root / "remote.git", self.root / "voices-clone"
        git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True)  # noqa: E731
        git("init", "-q", "--bare", "-b", "trunk", str(remote))
        git("clone", "-q", str(remote), str(site))
        git("-C", str(site), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first")
        git("-C", str(site), "push", "-q", "origin", "HEAD:refs/heads/trunk")
        self.assertEqual(voice.default_branch(site), "trunk")

    def test_render_hash_follows_text_voice_and_loudness(self):
        config, direction = voice.load_config(), {"hello": {"tags": "[whispers]", "gain_db": -5}}
        request = voice.request_for("hello", "Ciao.", "it", "clown", config, direction)
        self.assertEqual(request["text"], "Ciao.")
        config["audio_tags"] = True
        tagged = voice.request_for("hello", "Ciao.", "it", "clown", config, direction)
        self.assertEqual(tagged["text"], "[whispers] Ciao.")
        lufs = voice.loudness_for("hello", config, direction)
        self.assertEqual(lufs, -24)
        base = voice.render_hash("V1", tagged, lufs)
        self.assertNotEqual(base, voice.render_hash("V2", tagged, lufs))
        self.assertNotEqual(base, voice.render_hash("V1", request, lufs))

    def test_retake_changes_the_seed(self):
        config = voice.load_config()
        first = voice.request_for("joke.2", "x", "it", "clown", config, {})["seed"]
        config["languages"]["it"]["voices"]["clown"]["takes"] = {"joke.2": 2}
        self.assertNotEqual(first, voice.request_for("joke.2", "x", "it", "clown", config, {})["seed"])

    def test_mouth_envelope_follows_loudness(self):
        path = self.root / "tone.wav"
        rate = 8000
        quiet, loud = [0] * (rate // 4), [12000 if i % 2 else -12000 for i in range(rate // 4)]
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(array("h", quiet + loud + quiet).tobytes())
        duration, mouth = voice.analyze(path)
        self.assertAlmostEqual(duration, 0.75)
        self.assertTrue(mouth.startswith("000") and mouth.endswith("000"))
        self.assertIn("333", mouth)


if __name__ == "__main__":
    unittest.main()
