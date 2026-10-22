"""tools/store over the fixture app: check.py's limits, push.py against an in-memory App Store Connect (from
mybackhurts' test_push.py), import.py's Partner Center folder."""

from __future__ import annotations

import base64
import contextlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import TOOLS, FixtureAppTestCase

sys.path.insert(0, str(TOOLS / "store"))
import check  # noqa: E402
import listing as listing_module  # noqa: E402
import push  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def png(width, height, alpha=False, salt=b""):
    """A PNG's signature and IHDR chunk, enough for push.png_info."""
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6 if alpha else 2, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ihdr + salt


# The App Asset Library's reference data, trimmed: a header spec for 3840x1646 on App Store Connect's one group
# (DEFAULT_PROFILE, platform ANY), an iPhone and a Mac group, and one for another size; the search results asset's
# range on DEFAULT_PROFILE; the iPhone Duo's screenshot sizes.
HEADER_REF = {
    "features": [{"featureId": "APP_STORE_VERSIONS", "placementPolicies": [
        {"placementType": push.HEADER, "groupLimits": [
            {"groupIds": ["DEFAULT_PROFILE", "IPHONE_PROFILE", "MAC_PROFILE", "IPHONE_OTHER"], "maxCount": 1}]},
        {"placementType": push.SEARCH, "groupLimits": [{"groupIds": ["DEFAULT_PROFILE"], "maxCount": 1}]},
        {"placementType": push.SCREENSHOT, "groupLimits": [{"groupIds": [push.DUO, "MAC_PROFILE"], "maxCount": 10}]}]}],
    "placementProfileGroups": [{"placementProfileGroupId": g, "platform": p} for g, p in (
        ("DEFAULT_PROFILE", "ANY"), ("IPHONE_PROFILE", "IPHONE_APP_STORE"), ("MAC_PROFILE", "MAC_APP_STORE"),
        ("IPHONE_OTHER", "IPHONE_APP_STORE"), (push.DUO, "IPHONE_APP_STORE"))],
    "imageSpecs": [{"specId": "wide", "compatiblePlacementTypes": [push.HEADER], "dimensions": {
                        "minWidth": 3840, "maxWidth": 3840, "minHeight": 1646, "maxHeight": 1646}},
                   {"specId": "other", "compatiblePlacementTypes": [push.HEADER], "dimensions": {
                        "minWidth": 1920, "maxWidth": 1920, "minHeight": 1080, "maxHeight": 1080}},
                   {"specId": "search", "compatiblePlacementTypes": [push.SEARCH], "dimensions": {
                        "minWidth": 1920, "maxWidth": 3840, "minHeight": 1280, "maxHeight": 2560}},
                   {"specId": "duo", "compatiblePlacementTypes": [push.SCREENSHOT], "dimensions": {
                        "minWidth": 1398, "maxWidth": 1398, "minHeight": 2034, "maxHeight": 2034}}],
    "placementTypes": [{"placementTypeId": push.HEADER, "specMappings": [
        {"placementGroupId": "DEFAULT_PROFILE", "specs": ["wide"]}, {"placementGroupId": "IPHONE_PROFILE", "specs": ["wide"]},
        {"placementGroupId": "MAC_PROFILE", "specs": ["wide"]}, {"placementGroupId": "IPHONE_OTHER", "specs": ["other"]}]},
                       {"placementTypeId": push.SEARCH, "specMappings": [
        {"placementGroupId": "DEFAULT_PROFILE", "specs": ["search"]}]},
                       {"placementTypeId": push.SCREENSHOT, "specMappings": [
        {"placementGroupId": push.DUO, "specs": ["duo"]}, {"placementGroupId": "MAC_PROFILE", "specs": ["duo"]}]}],
}


class FakeApi:
    """An in-memory App Store Connect holding the objects push.py reads and writes."""

    def __init__(self, app_id):
        self.app_id = app_id
        self.objects = {}  # (kind, id) -> {"id", "type", "attributes", "parent"}
        self.calls = []
        self.next_id = 1

    def add(self, kind, attributes, parent=None):
        obj = {"id": str(self.next_id), "type": kind, "attributes": dict(attributes), "parent": parent}
        self.next_id += 1
        self.objects[(kind, obj["id"])] = obj
        return obj

    def children(self, kind, parent_id):
        return [o for (k, _), o in self.objects.items() if k == kind and o["parent"] == parent_id]

    def get_all(self, path):
        path, _, query = path.partition("?")
        filters = dict(x.split("=", 1) for x in query.split("&") if x.startswith("filter["))

        def matching(objects, names):
            return [o for o in objects if all(o["attributes"].get(attr, "MAC_OS" if attr == "platform" else None)
                                              == filters[key] for key, attr in names.items() if key in filters)]
        if path.endswith("/appStoreVersions"):
            return matching(self.children("appStoreVersions", self.app_id),
                            {"filter[platform]": "platform", "filter[versionString]": "versionString"})
        if path.endswith("/appInfos"):
            return self.children("appInfos", self.app_id)
        if path.endswith("/inAppPurchasesV2"):
            return self.children("inAppPurchases", self.app_id)
        if path == "/v1/builds":
            return matching(self.children("builds", self.app_id),
                            {"filter[version]": "version", "filter[preReleaseVersion.platform]": "platform"})
        kind = path.rsplit("/", 1)[1]
        parent_id = path.rsplit("/", 2)[1]
        if path.startswith("/v1/appAssetLibraries/") and kind == "images":
            kind = "appAssetLibraryImages"
        return self.children(kind, parent_id)

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/v1/appAssetLibraryRefData"):
            return {"data": [{"type": "appAssetLibraryRefData", "id": "1", "attributes": HEADER_REF}]}
        if method == "GET" and path.endswith("/assetLibrary"):
            return {"data": {"type": "appAssetLibraries", "id": "library"}}
        if method == "GET" and "/placements" in path:
            if path.startswith("/v1/appAssetLibraryImages/"):  # an image's placements, in every language
                image_id = path.split("/")[3]
                return {"data": [p for (k, _), p in self.objects.items() if k == "appAssetLibraryPlacements"
                                 and p["relationships"]["image"][1] == image_id]}
            placements = self.children("appAssetLibraryPlacements", path.split("/")[3])
            images = {p["relationships"]["image"][1] for p in placements}
            return {"data": [{**p, "relationships": {"image": {"data": {"type": "appAssetLibraryImages",
                                                                         "id": p["relationships"]["image"][1]}}}}
                             for p in placements],
                    "included": [self.objects[("appAssetLibraryImages", i)] for i in images]}
        if method == "GET" and path.endswith("/appStoreReviewScreenshot"):
            shots = self.children("inAppPurchaseAppStoreReviewScreenshots", path.rsplit("/", 2)[1])
            return {"data": shots[0] if shots else None}
        if method == "GET" and path.endswith("/appStoreReviewDetail"):
            details = self.children("appStoreReviewDetails", path.rsplit("/", 2)[1])
            return {"data": details[0] if details else None}
        if method == "GET":
            return {"data": None}
        return None

    def create(self, kind, attributes, relationships):
        self.calls.append(("POST", kind, attributes.get("locale") or attributes.get("fileName")))
        parent = next(iter(relationships.values()))[1]
        if "locale" in attributes and any(o["attributes"].get("locale") == attributes["locale"]
                                          for o in self.children(kind, parent)):
            raise push.ApiError(f"409 Entity with locale: {attributes['locale']} already exists.")
        if kind == "appAssetLibraryImages" and any(o["attributes"].get("referenceName") == attributes["referenceName"]
                                                   for o in self.children(kind, parent)):
            raise push.ApiError(f"409 Reference name '{attributes['referenceName']}' is already in use")
        obj = self.add(kind, attributes, parent)
        obj["relationships"] = relationships
        if kind == "appInfoLocalizations":  # like App Store Connect: the editable version gets the language too
            for version in self.children("appStoreVersions", self.app_id):
                self.add("appStoreVersionLocalizations", {"locale": attributes["locale"]}, version["id"])
        if kind in ("appScreenshots", "appPreviews", "appAssetLibraryImages",
                    "inAppPurchaseAppStoreReviewScreenshots"):
            obj["attributes"]["uploadOperations"] = []
        return obj

    def update(self, kind, id, attributes):
        self.calls.append(("PATCH", kind, tuple(sorted(attributes))))
        self.objects[(kind, id)]["attributes"].update(attributes)

    def delete(self, kind, id):
        self.calls.append(("DELETE", kind, id))
        del self.objects[(kind, id)]

    def upload(self, operations, data):
        pass


def discard(_line):
    """push.Sync's log, unread."""


class StoreTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.listing = listing_module.load(self.root)

    # MARK: The listing and check.py

    def test_every_language_has_a_locale_and_all_fields(self):
        self.assertEqual(set(self.listing.locale), set(self.listing.langs))
        for lang in self.listing.langs:
            fields = self.listing.fields(lang)
            info = push.info_fields(self.listing, lang, fields)
            for value in {**info, **push.version_fields(self.listing, lang, fields)}.values():
                self.assertTrue(value, lang)
            for spec in self.listing.iaps.values():
                self.assertTrue(fields[spec["name"]] and fields[spec["description"]], lang)

    def test_the_source_language_is_the_site_root(self):
        self.assertEqual(self.listing.site("en"), "https://fixture.example/")
        self.assertEqual(self.listing.site("de"), "https://fixture.example/de/")
        self.assertEqual(push.info_fields(self.listing, "it", {"apple.name": "", "apple.subtitle": ""})["privacyPolicyUrl"],
                         "https://fixture.example/it/privacy/")

    def test_the_limits_follow_the_config(self):
        limits = self.listing.limits
        self.assertEqual(limits["apple.iap.supporter.name"], 30)
        self.assertEqual(limits["apple.iap.supporter.description"], 45)
        self.assertIn("ms.captions", limits, "the app has store.microsoft")
        self.assertIn("apple.description", limits)
        self.assertNotIn("apple.description", self.listing.single_line)

    def test_check_passes_the_fixture_and_names_each_problem(self):
        self.assertTrue(check.run(self.listing, self.listing.langs))
        de = self.root / "store-listing" / "de.md"
        text = de.read_text("utf8").replace("Ein Freund am Schreibtisch", "Ein viel zu langer Untertitel hier")
        de.write_text(text.replace("begleiter,haustier", "begleiter, haustier"), "utf8")
        problems = check.check(self.listing, self.listing.fields("de"), self.listing.fields("en"))
        self.assertIn("apple.subtitle: 34 > 30 characters", problems)
        self.assertIn("apple.keywords: no spaces around commas (they count toward the limit)", problems)
        result = subprocess.run([sys.executable, str(TOOLS / "store" / "check.py"), "de"],
                                capture_output=True, encoding="utf8",
                                env={**os.environ, "COMPANION_ROOT": str(self.root)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("de: 2 problem(s)", result.stdout)

    def enable_ios(self):
        path = self.root / "companion.json"
        config = json.loads(path.read_text("utf8"))
        config["store"]["apple"]["ios"] = {}
        path.write_text(json.dumps(config), "utf8")
        self.listing = listing_module.load(self.root)

    def write_ios_page(self, keywords="companion,friend,pocket", whatsnew=None):
        en = self.root / "store-listing" / "en.md"
        en.write_text(en.read_text("utf8") + "\n".join([
            "### iOS promotional text `ios.promo`", "", "```text", "It says things, on the phone too.", "```", "",
            "### iOS keywords `ios.keywords`", "", "```text", keywords, "```", "",
            "### iOS description `ios.description`", "", "```text", "Fixture says a line now and then.", "",
            "Nothing leaves your iPhone.", "```", ""]
            + (["### iOS What's New `ios.whatsnew`", "", "```text", whatsnew, "```", ""] if whatsnew else [])), "utf8")

    def test_the_ios_page_is_checked_only_when_configured(self):
        self.assertNotIn("ios.promo", self.listing.limits)
        self.enable_ios()
        problems = check.check(self.listing, self.listing.fields("en"), self.listing.fields("en"))
        self.assertEqual(problems, ["missing field ios.promo", "missing field ios.keywords", "missing field ios.description"],
                         "What's New stays optional")
        self.write_ios_page()
        problems = check.check(self.listing, self.listing.fields("en"), self.listing.fields("en"))
        self.assertEqual(problems, ["ios.keywords: 'friend' is already in the name or subtitle"])
        self.assertNotIn("ios.description", self.listing.single_line)

    def enable_google(self):
        path = self.root / "companion.json"
        config = json.loads(path.read_text("utf8"))
        config["store"]["google"] = {}
        path.write_text(json.dumps(config), "utf8")
        self.listing = listing_module.load(self.root)

    def write_google_page(self, title="Fixture"):
        en = self.root / "store-listing" / "en.md"
        en.write_text(en.read_text("utf8") + "\n".join([
            "## Google Play", "",
            "### Title `google.title`", "", "```text", title, "```", "",
            "### Short description `google.short`", "", "```text", "A friend at the desk, in your pocket too.", "```", "",
            "### Full description `google.full`", "", "```text", "Fixture says a line now and then.", "",
            "Nothing leaves your phone.", "```", "",
            "### Supporter name `google.iap.supporter.name`", "", "```text", "Supporter", "```", "",
            "### Supporter description `google.iap.supporter.description`", "", "```text", "A thank-you.", "```", ""]), "utf8")

    def test_the_google_play_section_is_checked_only_when_configured(self):
        self.assertFalse({key for key in self.listing.limits if key.startswith("google.")})
        self.enable_google()
        limits = self.listing.limits
        self.assertEqual({key: limits[key] for key in limits if key.startswith("google.")}, {
            "google.title": 30, "google.short": 80, "google.full": 4000,
            "google.iap.supporter.name": 55, "google.iap.supporter.description": 200})
        self.assertNotIn("google.full", self.listing.single_line)
        problems = check.check(self.listing, self.listing.fields("en"), self.listing.fields("en"))
        self.assertEqual(problems, ["missing field google.title", "missing field google.short", "missing field google.full",
                                    "missing field google.iap.supporter.name",
                                    "missing field google.iap.supporter.description"])
        self.write_google_page()
        self.assertEqual(check.check(self.listing, self.listing.fields("en"), self.listing.fields("en")), [])
        self.write_google_page(title="Fixture, the friend who lives at your desk")
        problems = check.check(self.listing, self.listing.fields("en"), self.listing.fields("en"))
        self.assertIn("google.title: 42 > 30 characters", problems)
        result = subprocess.run([sys.executable, str(TOOLS / "store" / "check.py"), "en"],
                                capture_output=True, encoding="utf8",
                                env={**os.environ, "COMPANION_ROOT": str(self.root)})
        self.assertIn("Play title 42/30, short 41/80", result.stdout)

    # MARK: push.py

    def test_token_is_a_valid_es256_jwt(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, pub = Path(tmp) / "AuthKey_TEST.p8", Path(tmp) / "pub.pem"
            pem = subprocess.run("openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt",
                                 shell=True, capture_output=True, check=True).stdout
            key.write_bytes(pem)
            subprocess.run(["openssl", "ec", "-in", key, "-pubout", "-out", pub], capture_output=True, check=True)
            token = push.make_token("TEST", "issuer", key, now=1000)
            header, claims, signature = token.split(".")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
            self.assertEqual(json.loads(pad(header)), {"alg": "ES256", "kid": "TEST", "typ": "JWT"})
            self.assertEqual(json.loads(pad(claims))["aud"], "appstoreconnect-v1")
            raw = pad(signature)
            self.assertEqual(len(raw), 64)

            def integer(b):
                b = b.lstrip(b"\0")
                b = b"\0" + b if b[0] & 0x80 else b
                return bytes([2, len(b)]) + b
            body = integer(raw[:32]) + integer(raw[32:])
            sig = Path(tmp) / "sig.der"
            sig.write_bytes(bytes([0x30, len(body)]) + body)
            verify = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig],
                                    input=f"{header}.{claims}".encode(), capture_output=True)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_der_to_raw_pads_and_strips(self):
        r, s = b"\x00\x80" + b"\x01" * 31, b"\x05"
        der = bytes([0x30, 4 + len(r) + len(s), 2, len(r)]) + r + bytes([2, len(s)]) + s
        self.assertEqual(push.der_to_raw(der), b"\x80" + b"\x01" * 31 + b"\0" * 31 + b"\x05")

    def setup_app(self, earlier=True):
        api = FakeApi(self.listing.app_id)
        if earlier:
            api.add("appStoreVersions", {"platform": "MAC_OS", "versionString": "1.0.1", "appVersionState": "READY_FOR_SALE"},
                    self.listing.app_id)
        version = api.add("appStoreVersions", {"platform": "MAC_OS", "versionString": "1.0.2",
                                               "appVersionState": "PREPARE_FOR_SUBMISSION",
                                               "copyright": self.listing.copyright, "releaseType": "MANUAL"},
                          self.listing.app_id)
        info = api.add("appInfos", {"state": "PREPARE_FOR_SUBMISSION"}, self.listing.app_id)
        en = self.listing.fields("en")
        api.add("appInfoLocalizations", {"locale": "en-US", **push.info_fields(self.listing, "en", en)}, info["id"])
        stale = {**push.version_fields(self.listing, "en", en), "whatsNew": "old"}
        api.add("appStoreVersionLocalizations", {"locale": "en-US", **stale}, version["id"])
        return api, version

    def test_text_patches_only_what_changed_and_adds_missing_languages(self):
        api, version = self.setup_app()
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        result = sync.text(["en", "de"], sync.version("1.0.2", False, None), extra=False, write=True)
        self.assertEqual(sync.problems, [])
        self.assertIn(("PATCH", "appStoreVersionLocalizations", ("whatsNew",)), api.calls)
        self.assertIn(("POST", "appInfoLocalizations", "de-DE"), api.calls)
        # App Store Connect added de-DE to the version itself; push.py fills it in instead of adding it again.
        self.assertNotIn(("POST", "appStoreVersionLocalizations", "de-DE"), api.calls)
        self.assertEqual(api.children("appStoreVersionLocalizations", version["id"])[1]["attributes"]["whatsNew"],
                         self.listing.fields("de")["apple.whatsnew"])
        self.assertNotIn("PATCH", [c[0] for c in api.calls if c[1] == "appInfoLocalizations"])
        self.assertEqual(set(result), {"en-US", "de-DE"})

        api.calls.clear()
        sync.text(["en", "de"], version, extra=False, write=True)
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a second run changes nothing")

    def test_whats_new_is_left_out_on_a_first_version(self):
        api, version = self.setup_app(earlier=False)
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        sync.text(["de"], version, extra=False, write=True)
        de = next(x for x in api.children("appStoreVersionLocalizations", version["id"]) if x["attributes"]["locale"] == "de-DE")
        self.assertNotIn("whatsNew", de["attributes"])
        self.assertEqual(de["attributes"]["description"], self.listing.fields("de")["apple.description"])

    def test_the_ios_page_is_found_or_created_filled_and_given_its_screenshots(self):
        self.enable_ios()
        self.write_ios_page(whatsnew="Now on iPhone.")
        for folder, shots in ((self.listing.iphone_screenshots, ("1-phone.png",)),
                              (self.listing.ipad_screenshots, ("1-pad.png", "2-pad.png"))):
            (folder / "en").mkdir(parents=True)
            for name in shots:
                (folder / "en" / name).write_bytes(PNG + name.encode())
        api, mac = self.setup_app()
        api.add("builds", {"version": "4", "platform": "MAC_OS", "processingState": "VALID"}, self.listing.app_id)
        api.add("builds", {"version": "4", "platform": "IOS", "processingState": "VALID"}, self.listing.app_id)
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        with self.assertRaises(SystemExit, msg="no iOS version without --create"):
            sync.version("1.0.2", False, None, sync.ios)
        version = sync.version("1.0.2", True, "4", sync.ios)
        self.assertEqual(version["attributes"]["platform"], "IOS")
        self.assertNotEqual(version["id"], mac["id"])
        self.assertIn(("PATCH", f"/v1/appStoreVersions/{version['id']}/relationships/build"), api.calls)
        self.assertNotIn(("PATCH", f"/v1/appStoreVersions/{mac['id']}/relationships/build"), api.calls)

        localizations = sync.text(["en"], version, extra=False, write=True, page=sync.ios)
        sync.screenshots(localizations, sync.ios)
        self.assertEqual(sync.problems, [])
        en = localizations["en-US"][1]["attributes"]
        fields = self.listing.fields("en")
        self.assertEqual(en["description"], fields["ios.description"])
        self.assertEqual(en["keywords"], fields["ios.keywords"])
        self.assertEqual(en["promotionalText"], fields["ios.promo"])
        self.assertNotIn("whatsNew", en, "the platform's first version")
        sets = {s["attributes"]["screenshotDisplayType"]: s for s in api.children("appScreenshotSets", localizations["en-US"][1]["id"])}
        self.assertEqual(set(sets), {"APP_IPHONE_67", "APP_IPAD_PRO_3GEN_129"})
        self.assertEqual([x["attributes"]["fileName"] for x in api.children("appScreenshots", sets["APP_IPAD_PRO_3GEN_129"]["id"])],
                         ["1-pad.png", "2-pad.png"])
        self.assertEqual(api.children("appStoreVersionLocalizations", mac["id"])[0]["attributes"]["description"],
                         fields["apple.description"], "the Mac's page keeps its own text")

        api.calls.clear()
        sync.version("1.0.2", True, None, sync.ios)
        sync.screenshots(sync.text(["en"], version, extra=False, write=True, page=sync.ios), sync.ios)
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a second run changes nothing")

    def test_the_ios_parts_need_the_ios_config(self):
        result = subprocess.run([sys.executable, str(TOOLS / "store" / "push.py"), "--version", "1.0", "--only", "ios-text"],
                                capture_output=True, encoding="utf8",
                                env={**os.environ, "COMPANION_ROOT": str(self.root)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("need store.apple.ios", result.stderr)

    def test_a_push_for_one_platform_leaves_the_other_platforms_versions_alone(self):
        self.enable_ios()
        self.write_ios_page()
        api, _ = self.setup_app()

        def push_(*args):
            argv = ["push.py", "--create", "--apply", *args, "en"]
            with mock.patch.object(push, "load", return_value=self.listing), \
                    mock.patch.object(check, "run", return_value=True), mock.patch.object(push, "Client", return_value=api), \
                    mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                push.main()
            return {(v["attributes"]["platform"], v["attributes"]["versionString"])
                    for (k, _), v in api.objects.items() if k == "appStoreVersions"}

        created = push_("--version", "1.0.0", "--only", "ios-text,review")
        self.assertIn(("IOS", "1.0.0"), created)
        self.assertNotIn(("MAC_OS", "1.0.0"), created, "the iPhone's version number isn't the Mac's")
        created = push_("--version", "1.0.3", "--only", "text,review")
        self.assertIn(("MAC_OS", "1.0.3"), created)
        self.assertNotIn(("IOS", "1.0.3"), created, "the Mac's review notes don't make an iOS version")

    def test_dry_run_writes_nothing(self):
        api, _ = self.setup_app()
        sync = push.Sync(self.listing, api, apply=False, log=discard)
        localizations = sync.text(["en", "it"], sync.version("1.0.2", False, None), extra=False, write=True)
        sync.screenshots(localizations)
        sync.iaps(["en"], extra=False)
        self.assertEqual(api.calls, [])

    def test_screenshots_replace_a_stale_set_and_then_stay(self):
        folder = self.listing.screenshots / "en"
        folder.mkdir(parents=True)
        for name in ("1-home.png", "2-settings.png"):
            (folder / name).write_bytes(PNG + name.encode())
        api, version = self.setup_app()
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        localizations = sync.text(["en"], version, extra=False, write=False)
        locale = localizations["en-US"][1]
        shot_set = api.add("appScreenshotSets", {"screenshotDisplayType": self.listing.screenshot_type}, locale["id"])
        api.add("appScreenshots", {"fileName": "old.png", "sourceFileChecksum": "x"}, shot_set["id"])
        sync.screenshots(localizations)
        self.assertEqual(sync.problems, [])
        self.assertEqual(sum(c[0] == "DELETE" for c in api.calls), 1)
        self.assertEqual([c[2] for c in api.calls if c[:2] == ("POST", "appScreenshots")], ["1-home.png", "2-settings.png"])

        api.calls.clear()
        sync.screenshots(localizations)
        self.assertEqual(api.calls, [], "matching checksums leave the set alone")

    def test_extra_locales_only_when_present_or_asked(self):
        api, version = self.setup_app()
        sync = push.Sync(self.listing, api, apply=False, log=discard)
        self.assertEqual(set(sync.text(["en"], version, extra=False, write=False)), {"en-US"})
        api.add("appStoreVersionLocalizations", {"locale": "en-GB"}, version["id"])
        self.assertEqual(set(sync.text(["en"], version, extra=False, write=False)), {"en-US", "en-GB"})
        self.assertEqual(set(sync.text(["en"], version, extra=True, write=False)), {"en-US", "en-GB", "en-CA", "en-AU"})

    def test_missing_version_needs_create(self):
        api = FakeApi(self.listing.app_id)
        with self.assertRaises(SystemExit):
            push.Sync(self.listing, api, apply=False, log=discard).version("9.9", False, None)
        version = push.Sync(self.listing, api, apply=False, log=discard).version("9.9", True, None)
        self.assertIsNone(version["id"])
        self.assertEqual(api.calls, [])

    def test_the_review_notes_come_from_their_block(self):
        self.assertEqual(push.review_notes(self.listing), "Fixture lives in the menu bar. Click it for a line.")

    def test_without_review_notes_or_an_iap_note_and_shot_app_store_connect_keeps_its_own(self):
        self.listing.review_notes.unlink()
        for spec in self.listing.iaps.values():
            del spec["reviewNote"], spec["reviewShot"]
            spec["screenshot"] = None
        api, version = self.setup_app()
        for product_id in self.listing.iaps:
            api.add("inAppPurchases", {"productId": product_id, "reviewNote": "theirs"}, self.listing.app_id)
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        sync.review(version)
        sync.iaps(["en"], extra=False)
        self.assertEqual(sync.problems, [])
        self.assertNotIn("PATCH", [c[0] for c in api.calls], "no review note set")
        self.assertFalse([c for c in api.calls if "appStoreReviewScreenshot" in str(c) or "ReviewDetail" in str(c)])

    def add_ios_version(self, api):
        return api.add("appStoreVersions", {"platform": "IOS", "versionString": "1.0.2",
                                            "appVersionState": "PREPARE_FOR_SUBMISSION"}, self.listing.app_id)

    def test_the_review_notes_go_on_the_ios_version_too(self):
        self.enable_ios()
        api, mac = self.setup_app()
        ios = self.add_ios_version(api)
        api.add("appStoreReviewDetails", {"notes": "old"}, ios["id"])
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        with contextlib.redirect_stdout(io.StringIO()):
            sync.review(mac)
            sync.review(ios, sync.ios)
        self.assertEqual(sync.problems, [])
        notes = push.review_notes(self.listing)
        for version in (mac, ios):
            self.assertEqual([d["attributes"]["notes"] for d in api.children("appStoreReviewDetails", version["id"])],
                             [notes])
        self.assertIn(("POST", "appStoreReviewDetails", None), api.calls, "the Mac's is created")
        self.assertIn(("PATCH", "appStoreReviewDetails", ("notes",)), api.calls, "the iOS one is updated")

        api.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            sync.review(ios, sync.ios)
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a second run changes nothing")

    def status(self, api):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            push.status(self.listing, api, "1.0.2")
        return out.getvalue()

    def test_status_reports_the_ios_version_per_display_type(self):
        api, mac = self.setup_app()
        self.assertNotIn("iOS", self.status(api), "only with store.apple.ios")
        self.enable_ios()
        self.assertIn("Still needed: ", self.status(api))
        self.assertIn("the iOS version 1.0.2", self.status(api))

        ios = self.add_ios_version(api)
        api.add("appStoreReviewDetails", {"notes": "x", "contactFirstName": "A", "contactLastName": "B",
                                          "contactPhone": "1", "contactEmail": "a@b"}, ios["id"])
        en = api.add("appStoreVersionLocalizations", {"locale": "en-US", "description": "d", "keywords": "k"}, ios["id"])
        phone = api.add("appScreenshotSets", {"screenshotDisplayType": push.IPHONE_SCREENSHOTS}, en["id"])
        api.add("appScreenshots", {"fileName": "1.png"}, phone["id"])
        out = self.status(api)
        self.assertIn("iOS Version 1.0.2: PREPARE_FOR_SUBMISSION", out)
        self.assertIn('iOS en-US: 1 iPhone 6.9" screenshots, 0 iPad 13" screenshots, 0 header placements, 0 search results placements, '
                      'empty: supportUrl', out)
        needed = out.split("Still needed: ")[1]
        self.assertIn("a build on the iOS version page", needed)
        self.assertIn('iOS en-US iPad 13" screenshots', needed)
        self.assertIn("iOS en-US supportUrl", needed)
        self.assertNotIn('iOS en-US iPhone 6.9" screenshots', needed)
        self.assertNotIn("iOS App Review contact", needed)
        self.assertIn("the App Review contact", needed, "the Mac's has no details")
        self.assertIn("en-US: 0 screenshots", out, "the Mac's lines read as before")

    def test_status_reports_an_ios_version_the_mac_lacks(self):
        api, mac = self.setup_app(earlier=False)
        self.enable_ios()
        api.objects.pop(("appStoreVersions", mac["id"]))
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            push.status(self.listing, api, "1.0.2")
        self.assertIn("No version 1.0.2", str(raised.exception))

        self.add_ios_version(api)
        out = self.status(api)
        self.assertIn("No macOS version 1.0.2 in App Store Connect.", out)
        self.assertIn("iOS Version 1.0.2: PREPARE_FOR_SUBMISSION", out)
        self.assertIn("Still needed: ", out)

    def test_get_all_keeps_a_callers_limit(self):
        client, urls = push.Client.__new__(push.Client), []
        client.request = lambda method, url: urls.append(url) or {"data": []}
        client.get_all("/v1/builds?filter[app]=1&limit=20")
        client.get_all("/v1/builds?filter[app]=1")
        client.get_all("/v1/apps")
        self.assertEqual(urls, ["/v1/builds?filter[app]=1&limit=20", "/v1/builds?filter[app]=1&limit=200",
                                "/v1/apps?limit=200"])

    def flaky_urlopen(self, failures, calls):
        """An urlopen that times out `failures` times, then answers {} (as a context manager)."""
        def urlopen(req, timeout=None):
            calls.append(req.get_method())
            if len(calls) <= failures:
                raise push.urllib.error.URLError(TimeoutError(60, "Operation timed out"))
            return mock.MagicMock(**{"__enter__.return_value.read.return_value": b"{}"})
        return urlopen

    def test_a_timed_out_request_is_retried_unless_it_is_a_post(self):
        client = push.Client.__new__(push.Client)
        client.token, client.minted = "token", push.time.time()
        calls = []
        with mock.patch.object(push.urllib.request, "urlopen", self.flaky_urlopen(2, calls)), \
                mock.patch.object(push.time, "sleep"):
            client.update("appScreenshots", "1", {"uploaded": True})
        self.assertEqual(calls, ["PATCH"] * 3)

        calls.clear()
        with mock.patch.object(push.urllib.request, "urlopen", self.flaky_urlopen(1, calls)), \
                mock.patch.object(push.time, "sleep"), self.assertRaisesRegex(push.ApiError, "timed out"):
            client.request("POST", "/v1/appScreenshots", {})
        self.assertEqual(calls, ["POST"], "a POST that timed out may have gone through")

    def test_an_upload_chunk_is_retried_and_a_lasting_failure_is_an_api_error(self):
        client, calls = push.Client.__new__(push.Client), []
        operations = [{"url": "https://upload.example/1", "method": "PUT", "offset": 0, "length": 2}]
        with mock.patch.object(push.urllib.request, "urlopen", self.flaky_urlopen(3, calls)), \
                mock.patch.object(push.time, "sleep"):
            client.upload(operations, b"ab")
        self.assertEqual(calls, ["PUT"] * 4)

        calls.clear()
        with mock.patch.object(push.urllib.request, "urlopen", self.flaky_urlopen(9, calls)), \
                mock.patch.object(push.time, "sleep"), self.assertRaises(push.ApiError):
            client.upload(operations, b"ab")
        self.assertEqual(len(calls), 5)

    def configure(self, **apple):
        path = self.root / "companion.json"
        config = json.loads(path.read_text("utf8"))
        for key, value in apple.items():
            config["store"]["apple"][key] = value
        path.write_text(json.dumps(config), "utf8")
        self.listing = listing_module.load(self.root)

    def test_the_release_type_follows_the_config_on_an_editable_version(self):
        api, version = self.setup_app()
        push.Sync(self.listing, api, apply=True, log=discard).version("1.0.2", False, None)
        self.assertEqual(version["attributes"]["releaseType"], "MANUAL", "without the config it's left alone")

        self.configure(releaseType="AFTER_APPROVAL")
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        sync.version("1.0.2", False, None)
        self.assertEqual(version["attributes"]["releaseType"], "AFTER_APPROVAL")
        created = sync.version("1.0.3", True, None)
        self.assertEqual(created["attributes"]["releaseType"], "AFTER_APPROVAL")

        live = api.objects[("appStoreVersions", "1")]  # 1.0.1, READY_FOR_SALE
        live["attributes"]["releaseType"] = "MANUAL"
        api.calls.clear()
        sync.version("1.0.1", False, None)
        self.assertEqual(live["attributes"]["releaseType"], "MANUAL", "a locked version isn't touched")

    def header_app(self):
        self.enable_ios()
        api, _ = self.setup_app()
        ios = self.add_ios_version(api)
        for locale in ("en-US", "de-DE"):
            api.add("appStoreVersionLocalizations", {"locale": locale}, ios["id"])
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        return api, sync, ios

    def placed(self, api, ios, placement_type=push.HEADER):
        return sorted((loc["attributes"]["locale"], p["attributes"]["placementGroup"],
                       api.objects[("appAssetLibraryImages", p["relationships"]["image"][1])]["attributes"]["fileName"])
                      for loc in api.children("appStoreVersionLocalizations", ios["id"])
                      for p in api.children("appAssetLibraryPlacements", loc["id"])
                      if p["attributes"]["placementType"] == placement_type)

    def test_the_header_is_uploaded_once_and_placed_on_every_ios_language(self):
        api, sync, ios = self.header_app()
        header = self.listing.ios_header
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_bytes(png(3840, 1646))
        sync.header(sync.text(["en", "de"], ios, extra=False, write=False, page=sync.ios))
        self.assertEqual(sync.problems, [])
        images = [o for (k, _), o in api.objects.items() if k == "appAssetLibraryImages"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["attributes"]["category"], "CREATIVE_ASSETS")
        self.assertTrue(images[0]["attributes"]["uploaded"])
        name = images[0]["attributes"]["fileName"]
        self.assertEqual(self.placed(api, ios), [(locale, group, name) for locale in ("de-DE", "en-US")
                                                 for group in ("DEFAULT_PROFILE", "IPHONE_PROFILE")],
                         "the groups that take the size, not the Mac's or another size's")

        api.calls.clear()
        sync.header(sync.text(["en", "de"], ios, extra=False, write=False, page=sync.ios))
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a second run changes nothing")

        header.write_bytes(png(3840, 1646, salt=b"new"))
        sync.header(sync.text(["en", "de"], ios, extra=False, write=False, page=sync.ios))
        self.assertEqual(sync.problems, [])
        self.assertEqual(len([k for k, _ in api.objects if k == "appAssetLibraryImages"]), 2, "one more upload")
        placed = self.placed(api, ios)
        self.assertEqual(len(placed), 4)
        self.assertNotIn(name, {f for _, _, f in placed}, "the old header is replaced everywhere")

    def test_a_header_with_alpha_or_the_wrong_size_is_refused(self):
        api, sync, ios = self.header_app()
        header = self.listing.ios_header
        header.parent.mkdir(parents=True, exist_ok=True)
        for data, problem in ((png(3840, 1646, alpha=True), "alpha"), (png(3000, 1200), "3000x1200")):
            header.write_bytes(data)
            sync.problems.clear()
            sync.header(sync.text(["en"], ios, extra=False, write=False, page=sync.ios))
            self.assertEqual(len(sync.problems), 1)
            self.assertIn(problem, sync.problems[0])
        self.assertEqual([k for k, _ in api.objects if k.startswith("appAssetLibrary")], [])

    def test_the_search_results_asset_is_placed_like_the_header(self):
        api, sync, ios = self.header_app()
        search = self.listing.ios_search
        search.parent.mkdir(parents=True, exist_ok=True)
        search.write_bytes(png(3840, 2560))
        self.listing.ios_header.write_bytes(png(3840, 1646))
        localizations = sync.text(["en", "de"], ios, extra=False, write=False, page=sync.ios)
        sync.header(localizations)
        sync.search_results(localizations)
        self.assertEqual(sync.problems, [])
        name = next(o["attributes"]["fileName"] for (k, _), o in api.objects.items()
                    if k == "appAssetLibraryImages" and o["attributes"]["fileName"].startswith("search-"))
        self.assertEqual(self.placed(api, ios, push.SEARCH), [(locale, "DEFAULT_PROFILE", name)
                                                              for locale in ("de-DE", "en-US")])
        self.assertEqual(len(self.placed(api, ios)), 4, "the header stays where it was")

        api.calls.clear()
        sync.header(localizations)
        sync.search_results(localizations)
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a second run changes nothing")

        search.write_bytes(png(1000, 800))
        sync.search_results(localizations)
        self.assertEqual(len(sync.problems), 2)
        self.assertIn("1000x800", sync.problems[0])

    def test_duo_screenshots_are_placed_per_language_in_order(self):
        api, sync, ios = self.header_app()
        for lang, salts in (("en", (b"1", b"2")), ("de", (b"3",))):
            folder = self.listing.duo_screenshots / lang
            folder.mkdir(parents=True, exist_ok=True)
            for i, salt in enumerate(salts):
                (folder / f"{i + 1}-shot.png").write_bytes(png(1398, 2034, salt=salt))
        localizations = sync.text(["en", "de"], ios, extra=False, write=False, page=sync.ios)
        sync.duo_screenshots(localizations)
        self.assertEqual(sync.problems, [])
        en = [c[2] for c in api.calls if c[:2] == ("POST", "appAssetLibraryImages")]
        self.assertEqual(len(en), 3, "each file uploaded once")
        placed = self.placed(api, ios, push.SCREENSHOT)
        self.assertEqual([(locale, group) for locale, group, _ in placed],
                         [("de-DE", push.DUO), ("en-US", push.DUO), ("en-US", push.DUO)], "the Duo's group, not the Mac's")

        api.calls.clear()
        sync.duo_screenshots(localizations)
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a second run changes nothing")

        for p in [k for k in api.objects if k[0] == "appAssetLibraryPlacements"]:
            del api.objects[p]
        api.calls.clear()
        sync.duo_screenshots(localizations)
        self.assertEqual([c for c in api.calls if c[:2] == ("POST", "appAssetLibraryImages")], [],
                         "a later run places the images already in the library instead of uploading them again")
        self.assertEqual(len(self.placed(api, ios, push.SCREENSHOT)), 3)

        (self.listing.duo_screenshots / "en" / "2-shot.png").write_bytes(png(1320, 2868))
        sync.duo_screenshots(localizations)
        self.assertEqual(len(sync.problems), 1)
        self.assertIn("1320x2868", sync.problems[0])

    def test_an_image_stuck_awaiting_upload_is_deleted_and_uploaded_again(self):
        """An upload App Store Connect left in AWAITING_UPLOAD (its commit lost, no checksum) never finishes: each flow
        retries it like a FAILED one instead of keeping it because its name or checksum matches."""
        stuck = {"state": "AWAITING_UPLOAD", "sourceFileChecksum": ""}

        api, sync, ios = self.header_app()
        folder = self.listing.duo_screenshots / "en"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "1-shot.png").write_bytes(png(1398, 2034))
        (self.listing.duo_screenshots / "de").mkdir()
        (self.listing.duo_screenshots / "de" / "1-shot.png").write_bytes(png(1398, 2034))
        localizations = sync.text(["en", "de"], ios, extra=False, write=False, page=sync.ios)
        sync.duo_screenshots(localizations)
        [image] = [o for (k, _), o in api.objects.items() if k == "appAssetLibraryImages"]
        image["attributes"].update(stuck)
        api.calls.clear()
        sync.duo_screenshots(localizations)
        self.assertEqual(sync.problems, [])
        self.assertIn(("DELETE", "appAssetLibraryImages", image["id"]), api.calls)
        self.assertEqual(len([c for c in api.calls if c[:2] == ("DELETE", "appAssetLibraryPlacements")]), 2,
                         "its placements in both languages go with it")
        [again] = [o for (k, _), o in api.objects.items() if k == "appAssetLibraryImages"]
        self.assertNotEqual(again["id"], image["id"])
        self.assertEqual(again["attributes"]["fileName"], image["attributes"]["fileName"])
        self.assertEqual({p["relationships"]["image"][1] for (k, _), p in api.objects.items()
                          if k == "appAssetLibraryPlacements"}, {again["id"]}, "both languages placed again")
        again["attributes"]["state"] = "UPLOAD_COMPLETE"
        api.calls.clear()
        sync.duo_screenshots(localizations)
        self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "a processing image is left alone")

        folder = self.listing.screenshots / "en"
        folder.mkdir(parents=True)
        (folder / "1-home.png").write_bytes(PNG)
        api, version = self.setup_app()
        sync = push.Sync(self.listing, api, apply=True, log=discard)
        locale = sync.text(["en"], version, extra=False, write=False)["en-US"][1]
        shot_set = api.add("appScreenshotSets", {"screenshotDisplayType": self.listing.screenshot_type}, locale["id"])
        shot = api.add("appScreenshots", {"fileName": "1-home.png", "sourceFileChecksum": push.md5(folder / "1-home.png"),
                                          "assetDeliveryState": {"state": "AWAITING_UPLOAD"}}, shot_set["id"])
        sync.screenshots({"en-US": ("en", locale)})
        self.assertIn(("DELETE", "appScreenshots", shot["id"]), api.calls, "a matching checksum doesn't keep it")
        self.assertEqual([c[2] for c in api.calls if c[:2] == ("POST", "appScreenshots")], ["1-home.png"])

        capture = self.listing.dir / "review.png"
        capture.write_bytes(PNG)
        product = api.add("inAppPurchases", {"productId": "tip"}, self.listing.app_id)
        kind = "inAppPurchaseAppStoreReviewScreenshots"
        old = api.add(kind, {"fileName": "review.png", "sourceFileChecksum": "",
                             "assetDeliveryState": {"state": "AWAITING_UPLOAD"}}, product["id"])
        api.calls.clear()
        with mock.patch.object(push, "review_image", lambda _listing, f: f):
            sync.iap_review_screenshot(product, capture)
            self.assertEqual([c[:2] for c in api.calls if c[0] != "GET"],
                             [("DELETE", kind), ("POST", kind), ("PATCH", kind)])
            self.assertNotIn((kind, old["id"]), api.objects)
            api.calls.clear()
            sync.iap_review_screenshot(product, capture)
            self.assertEqual([c for c in api.calls if c[0] != "GET"], [], "the new upload is kept")

    # MARK: import.py

    def test_import_builds_a_partner_center_folder(self):
        store = self.root / "store-listing"
        for lang in self.listing.langs:
            folder = store / "microsoft" / "screenshots" / lang
            folder.mkdir(parents=True)
            for name in ("1.png", "2.png"):
                (folder / name).write_bytes(PNG)
        art = store / "microsoft" / "art"
        art.mkdir(parents=True)
        for name in ("poster-art.png", "box-art.png", "super-hero-art.png"):
            (art / name).write_bytes(PNG)
        out = self.root / "build" / "import"
        result = subprocess.run([sys.executable, str(TOOLS / "store" / "import.py"), "--out", str(out)],
                                capture_output=True, encoding="utf8",
                                env={**os.environ, "COMPANION_ROOT": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out / "listings.csv", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Field", "ID", "Type (Type)", "default", "en", "it", "de"])
        values = {row[0]: row[4:] for row in rows[1:]}
        self.assertEqual(values["Title"], ["Fixture", "Fixture", "Fixture"])
        self.assertEqual(values["DevStudio"], ["Fixture Studio"] * 3)
        self.assertEqual(values["DesktopScreenshot1"][1], "import/it/1.png")
        self.assertEqual(values["DesktopScreenshotCaption2"][2], "Einstellungen")
        self.assertTrue((out / "art" / "box-art.png").exists())

    def test_import_refuses_a_language_without_its_screenshots(self):
        result = subprocess.run([sys.executable, str(TOOLS / "store" / "import.py"), "--out", str(self.root / "out")],
                                capture_output=True, encoding="utf8",
                                env={**os.environ, "COMPANION_ROOT": str(self.root)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("en: 0 screenshots", result.stderr)

    def test_import_wants_one_screenshot_per_shot(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["store"]["microsoft"] = {"publisher": "Fixture Studio", "shots": ["panel-light", "home-light", "panel-dark"]}
        path.write_text(json.dumps(data), "utf8")
        result = subprocess.run([sys.executable, str(TOOLS / "store" / "import.py"), "--out", str(self.root / "out")],
                                capture_output=True, encoding="utf8",
                                env={**os.environ, "COMPANION_ROOT": str(self.root)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("en: 0 screenshots in", result.stderr)
        self.assertIn("want 3", result.stderr)


if __name__ == "__main__":
    unittest.main()
