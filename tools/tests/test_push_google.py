"""tools/store/push-google.py over the fixture app, without Google Play: the token request's signature, the plan a dry
run prints and what prepare --apply sends to a fake API."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import TOOLS, FixtureAppTestCase, quiet

spec = importlib.util.spec_from_file_location("push_google", TOOLS / "store" / "push-google.py")
push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
GOOGLE_PAGE = [
    "## Google Play", "",
    "### Title `google.title`", "", "```text", "Fixture", "```", "",
    "### Short description `google.short`", "", "```text", "A friend at the desk.", "```", "",
    "### Full description `google.full`", "", "```text", "Fixture says a line now and then.", "```", "",
    "### Supporter name `google.iap.supporter.name`", "", "```text", "Supporter", "```", "",
    "### Supporter description `google.iap.supporter.description`", "", "```text", "A thank-you.", "```", ""]


class FakeApi:
    """Answers like the Play Developer API from `listings`, `images` and `products`, and records every call."""

    def __init__(self, listings=(), images=None, products=()):
        self.package = "app.fixture"
        self.listings, self.images, self.products = list(listings), images or {}, list(products)
        self.calls = []

    def call(self, method, path, body=None, query=None, data=None, base=push.API):
        self.calls.append((method, path, body, query, data))
        if (method, path) == ("POST", "edits"):
            return {"id": "e1"}
        if (method, path) == ("GET", "edits/e1/listings"):
            return {"listings": self.listings}
        if method == "GET" and path.startswith("edits/e1/listings/"):
            return {"images": self.images.get(path.removeprefix("edits/e1/listings/"), [])}
        if (method, path) == ("GET", "oneTimeProducts"):
            return {"oneTimeProducts": self.products}
        if path == "pricing:convertRegionPrices":
            return {"regionVersion": {"version": "2025/03"},
                    "convertedRegionPrices": {"IT": {"regionCode": "IT", "price": {"currencyCode": "EUR", "units": "1"}}}}
        return {}

    def paths(self, method):
        return [path for m, path, *_ in self.calls if m == method]


class PushGoogleTestCase(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        path = self.root / "companion.json"
        config = json.loads(path.read_text("utf8"))
        config["store"]["google"] = {"packageName": "app.fixture", "locales": {"en": "en-US"}}
        path.write_text(json.dumps(config), "utf8")
        en = self.root / "store-listing" / "en.md"
        en.write_text(en.read_text("utf8") + "\n".join(GOOGLE_PAGE), "utf8")
        google = self.root / "store-listing" / "google"
        (google / "phone" / "en").mkdir(parents=True)
        (google / "tablet" / "en").mkdir(parents=True)
        for file in (google / "icon-512.png", google / "feature-graphic.png", google / "phone" / "en" / "1-a.png",
                     google / "phone" / "en" / "2-b.png", google / "tablet" / "en" / "1-a.png"):
            file.write_bytes(PNG)
        self.store = push.load(self.root)

    def prepare(self, api, only="text,graphics,products", langs=("en",), apply=True, not_sent=False):
        args = argparse.Namespace(only=only, langs=list(langs), apply=apply, not_sent_for_review=not_sent)
        return quiet(push.cmd_prepare, api, self.store, args)[1]


class ConfigTests(PushGoogleTestCase):
    def test_locales_fall_back_to_apples_then_the_language(self):
        self.assertEqual(push.locale(self.store, "en"), "en-US")
        self.assertEqual(push.locale(self.store, "de"), "de-DE")
        self.assertEqual(push.locale(self.store, "it"), "it")

    def test_graphics_of_the_source_language_and_the_others(self):
        en = push.graphics(self.store, "en")
        self.assertEqual(list(en), list(push.IMAGES))
        self.assertEqual([file.name for file in en["phoneScreenshots"]], ["1-a.png", "2-b.png"])
        self.assertEqual(en["sevenInchScreenshots"], en["tenInchScreenshots"])
        self.assertEqual(push.graphics(self.store, "it"), {}, "a language without folders shows the source's")

    def test_money_is_units_and_nanos(self):
        self.assertEqual(push.money("0.99"), {"currencyCode": "USD", "units": "0", "nanos": 990000000})
        self.assertEqual(push.money("10"), {"currencyCode": "USD", "units": "10", "nanos": 0})

    def test_products_take_the_google_fields(self):
        self.assertEqual(push.products(self.store)["app.fixture.supporter"], {
            "listings": [{"languageCode": "en-US", "title": "Supporter", "description": "A thank-you."}],
            "price": "1.99"})


class PrepareTests(PushGoogleTestCase):
    def test_a_dry_run_without_credentials_lists_the_local_files(self):
        out = self.prepare(None, apply=False)
        self.assertIn("no credentials", out)
        self.assertIn("text en-US: update title, shortDescription, fullDescription", out)
        self.assertIn("graphics en-US phoneScreenshots: 1-a.png, 2-b.png", out)
        self.assertIn("product app.fixture.supporter: create, 1 languages, USD 1.99", out)

    def test_a_dry_run_against_play_writes_nothing(self):
        api = FakeApi()
        self.prepare(api, apply=False)
        self.assertEqual(api.paths("PUT") + api.paths("PATCH") + [p for p in api.paths("POST") if p != "edits"], [])
        self.assertEqual(api.paths("DELETE"), ["edits/e1"], "the unused edit is deleted")

    def test_apply_writes_the_text_and_graphics_then_commits(self):
        api = FakeApi()
        self.prepare(api, only="text,graphics")
        body = next(body for method, path, body, *_ in api.calls if method == "PUT")
        self.assertEqual(body, {"language": "en-US", "title": "Fixture", "shortDescription": "A friend at the desk.",
                                "fullDescription": "Fixture says a line now and then."})
        uploads = [path for path in api.paths("POST") if "/listings/" in path]
        self.assertEqual(uploads.count("edits/e1/listings/en-US/phoneScreenshots"), 2)
        self.assertEqual(uploads.count("edits/e1/listings/en-US/tenInchScreenshots"), 1)
        self.assertEqual(api.paths("POST")[-1], "edits/e1:commit")
        self.assertNotIn("edits/e1", api.paths("DELETE"), "a committed edit isn't deleted")

    def test_matching_text_and_graphics_are_left_alone(self):
        digest = hashlib.sha256(PNG).hexdigest()
        listing = {"language": "en-US", "title": "Fixture", "shortDescription": "A friend at the desk.",
                   "fullDescription": "Fixture says a line now and then."}
        images = {f"en-US/{kind}": [{"sha256": digest}] * len(files)
                  for kind, files in push.graphics(self.store, "en").items()}
        api = FakeApi(listings=[listing], images=images)
        self.prepare(api, only="text,graphics")
        self.assertEqual(api.paths("PUT"), [])
        self.assertEqual([path for path in api.paths("POST") if "/listings/" in path], [])

    def test_not_sent_for_review_goes_on_the_commit(self):
        api = FakeApi()
        self.prepare(api, only="text", not_sent=True)
        commit = next(call for call in api.calls if call[1] == "edits/e1:commit")
        self.assertEqual(commit[3], {"changesNotSentForReview": "true"})

    def test_a_new_product_is_priced_in_every_region_and_activated(self):
        api = FakeApi()
        self.prepare(api, only="products")
        _, path, body, query, _ = next(call for call in api.calls if call[0] == "PATCH")
        self.assertEqual(path, "onetimeproducts/app.fixture.supporter")
        self.assertEqual(query["regionsVersion.version"], "2025/03")
        self.assertEqual(body["purchaseOptions"][0]["regionalPricingAndAvailabilityConfigs"],
                         [{"regionCode": "IT", "price": {"currencyCode": "EUR", "units": "1"}, "availability": "AVAILABLE"}])
        self.assertIn("oneTimeProducts/app.fixture.supporter/purchaseOptions:batchUpdateStates", api.paths("POST"))

    def test_an_active_product_is_not_activated_again(self):
        api = FakeApi(products=[{"productId": "app.fixture.supporter",
                                 "purchaseOptions": [{"purchaseOptionId": "buy", "state": "ACTIVE"}]}])
        self.prepare(api, only="products")
        self.assertEqual(len(api.paths("PATCH")), 1)
        self.assertFalse([path for path in api.paths("POST") if "batchUpdateStates" in path])


@unittest.skipUnless(shutil.which("openssl"), "needs openssl")
class AssertionTests(unittest.TestCase):
    def test_the_token_request_is_signed_with_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, public = Path(tmp) / "key.pem", Path(tmp) / "public.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out",
                            key], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", public], check=True, capture_output=True)
            jwt = push.assertion({"client_email": "push@fixture.iam", "token_uri": "https://oauth2.googleapis.com/token",
                                  "private_key": key.read_text()}, now=1000)
            header, claims, signature = jwt.split(".")
            decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
            self.assertEqual(json.loads(decode(header)), {"alg": "RS256", "typ": "JWT"})
            self.assertEqual(json.loads(decode(claims))["exp"], 4600)
            (Path(tmp) / "sig").write_bytes(decode(signature))
            (Path(tmp) / "data").write_text(f"{header}.{claims}")
            verify = subprocess.run(["openssl", "dgst", "-sha256", "-verify", public, "-signature", Path(tmp) / "sig",
                                     Path(tmp) / "data"], capture_output=True, text=True)
            self.assertIn("Verified OK", verify.stdout)


if __name__ == "__main__":
    unittest.main()
