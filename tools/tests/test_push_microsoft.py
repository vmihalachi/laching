"""tools/store/push-microsoft.py over the fixture app, without Partner Center (from mybackhurts' test_push.py): the
bundle check, the plan a submission becomes, the API client and prepare against a fake API."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from support import TOOLS, FixtureAppTestCase

spec = importlib.util.spec_from_file_location("push_microsoft", TOOLS / "store" / "push-microsoft.py")
push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
CREDENTIALS = {"MS_STORE_TENANT_ID": "t", "MS_STORE_CLIENT_ID": "c", "MS_STORE_CLIENT_SECRET": "s"}


def make_bundle(folder, version="1.0.7.0", files=("Fixture.exe", "resources.pri")):
    """A .msixbundle shaped like makeappx's: the manifest plus one .msix per platform, each with its AppxManifest.xml."""
    path = Path(folder) / f"Fixture_{version}_x64_arm64.msixbundle"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("AppxMetadata/AppxBundleManifest.xml",
                        f'<Bundle><Identity Name="Fixture.Fixture" Version="{version}" /></Bundle>')
        for arch in ("x64", "arm64"):
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as package:
                package.writestr("AppxManifest.xml", '<Package><Applications><Application Id="App" '
                                                     'Executable="Fixture.exe" /></Applications></Package>')
                for name in files:
                    package.writestr(name, "x")
            bundle.writestr(f"Fixture.App_{version}_{arch}.msix", inner.getvalue())
    return path


def published(listing_key="en", shots=2):
    """The last published submission, as GET returns it (fields the plan doesn't touch left out)."""
    images = [{"fileName": f"old{i}.png", "fileStatus": "Uploaded", "id": str(100 + i),
               "description": f"old caption {i}", "imageType": "Screenshot"} for i in range(shots)]
    images.append({"fileName": "poster.png", "fileStatus": "Uploaded", "id": "200", "imageType": "StoreLogo9x16"})
    return {
        "id": "1", "friendlyName": "Submission 4", "status": "Release", "visibility": "Public",
        "targetPublishMode": "Immediate", "pricing": {"priceId": "Free"}, "notesForCertification": "old notes",
        "listings": {listing_key: {"baseListing": {"title": "fixture", "description": "old", "features": [],
                                                   "keywords": [], "images": images}, "platformOverrides": {}}},
        "applicationPackages": [
            {"fileName": "Fixture_1.0.6.0_x64_arm64.msixbundle", "fileStatus": "Uploaded", "id": "9",
             "version": "1.0.6.0", "architecture": "Neutral"}],
    }


def quiet():
    return lambda *_: None


class BundleTests(unittest.TestCase):
    def test_reads_the_version_and_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            version, packages, problems = push.bundle_info(make_bundle(tmp))
        self.assertEqual(version, "1.0.7.0")
        self.assertEqual(len(packages), 2)
        self.assertEqual(problems, [])

    def test_refuses_a_package_that_isnt_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, problems = push.bundle_info(make_bundle(tmp, files=("Fixture.dll", "Fixture.runtimeconfig.json")))
        self.assertTrue(any("no Fixture.exe" in p for p in problems), problems)
        self.assertTrue(any("runtime" in p for p in problems), problems)


class StoreTestCase(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.store = push.Store(push.load(self.root))
        self.en = self.store.fields("en")
        folder = self.store.screenshots / "en"
        folder.mkdir(parents=True)
        for name in ("1-panel-light.png", "2-settings-light.png"):
            (folder / name).write_bytes(PNG)
        self.store.art.mkdir(parents=True)
        for file in push.ART.values():
            (self.store.art / file).write_bytes(PNG)


class PlanTests(StoreTestCase):
    def plan(self, parts, sub=None, langs=("en",), version="1.0.7.0", notes=None):
        bundle = Path("Fixture_1.0.7.0_x64_arm64.msixbundle")
        plan = push.Plan(self.store, sub or published(), set(parts), list(langs), bundle, version, notes, log=quiet())
        return plan, plan.build()

    def test_the_store_comes_from_companion_json(self):
        self.assertEqual(self.store.app_id, "9NFIXTURE001")
        self.assertEqual(self.store.publisher, "Fixture Studio")
        self.assertEqual(self.store.count, 2, "one screenshot per store.microsoft.shots")

    def test_package_replaces_every_old_package(self):
        plan, sub = self.plan({"package"})
        packages = sub["applicationPackages"]
        self.assertEqual(packages[0]["fileStatus"], "PendingDelete")
        self.assertEqual(packages[1], {"fileName": "Fixture_1.0.7.0_x64_arm64.msixbundle",
                                       "fileStatus": "PendingUpload", "minimumDirectXVersion": "None",
                                       "minimumSystemRam": "None"})
        self.assertEqual(list(plan.files), ["Fixture_1.0.7.0_x64_arm64.msixbundle"])
        self.assertEqual(plan.problems, [])

    def test_package_refuses_a_version_that_isnt_higher(self):
        plan, _ = self.plan({"package"}, version="1.0.6.0")
        self.assertIn("bump Package.appxmanifest", plan.problems[0])

    def test_the_input_is_not_modified(self):
        sub = published()
        self.plan({"package", "text", "screenshots", "art"}, sub=sub)
        self.assertEqual(sub, published())

    def test_text_fills_the_listing_from_the_markdown(self):
        plan, sub = self.plan({"text"})
        base = sub["listings"]["en"]["baseListing"]
        self.assertEqual(base["title"], self.en["ms.name"])
        self.assertEqual(base["description"], self.en["ms.description"])
        self.assertEqual(base["features"], self.en["ms.features"].split("\n"))
        self.assertEqual(base["keywords"], self.en["ms.search"].split("\n"))
        self.assertEqual(base["copyrightAndTrademarkInfo"], self.en["ms.copyright"])
        self.assertEqual(base["devStudio"], "Fixture Studio")
        self.assertEqual(plan.files, {})

    def test_text_retitles_the_uploaded_screenshots(self):
        _, sub = self.plan({"text"})
        shots = [i for i in sub["listings"]["en"]["baseListing"]["images"] if i["imageType"] == "Screenshot"]
        self.assertEqual([s["description"] for s in shots], self.en["ms.captions"].split("\n"))
        self.assertTrue(all(s["fileStatus"] == "Uploaded" for s in shots))

    def test_text_leaves_captions_alone_when_the_counts_differ(self):
        _, sub = self.plan({"text"}, sub=published(shots=5))
        shots = [i for i in sub["listings"]["en"]["baseListing"]["images"] if i["imageType"] == "Screenshot"]
        self.assertEqual(shots[0]["description"], "old caption 0")

    def test_finds_the_listing_whatever_its_case(self):
        _, sub = self.plan({"text"}, sub=published(listing_key="EN"))
        self.assertEqual(list(sub["listings"]), ["EN"])
        self.assertEqual(sub["listings"]["EN"]["baseListing"]["title"], self.en["ms.name"])

    def test_adds_a_missing_language(self):
        _, sub = self.plan({"text"}, langs=("en", "de"))
        self.assertIn("de", sub["listings"])
        self.assertEqual(sub["listings"]["de"]["baseListing"]["title"], self.store.fields("de")["ms.name"])

    def test_screenshots_replace_the_old_ones_in_order_with_captions(self):
        plan, sub = self.plan({"screenshots"})
        images = sub["listings"]["en"]["baseListing"]["images"]
        old = [i for i in images if i.get("id") and i["imageType"] == "Screenshot"]
        new = [i for i in images if i["fileStatus"] == "PendingUpload"]
        self.assertTrue(all(i["fileStatus"] == "PendingDelete" for i in old))
        self.assertEqual([i["fileName"] for i in new], ["en-1-panel-light.png", "en-2-settings-light.png"])
        self.assertEqual([i["description"] for i in new], self.en["ms.captions"].split("\n"))
        self.assertEqual(set(plan.files), {i["fileName"] for i in new})
        poster = next(i for i in images if i["imageType"] == "StoreLogo9x16")
        self.assertEqual(poster["fileStatus"], "Uploaded", "art isn't part of screenshots")

    def test_screenshots_want_one_per_shot(self):
        plan, _ = self.plan({"screenshots"}, langs=("it",))
        self.assertTrue(any("it: 0 screenshots" in p and "want 2" in p for p in plan.problems), plan.problems)

    def test_art_replaces_the_three_slots(self):
        plan, sub = self.plan({"art"})
        images = sub["listings"]["en"]["baseListing"]["images"]
        new = {i["imageType"]: i["fileName"] for i in images if i["fileStatus"] == "PendingUpload"}
        self.assertEqual(new, {kind: f"art-{file}" for kind, file in push.ART.items()})
        self.assertEqual(next(i for i in images if i.get("id") == "200")["fileStatus"], "PendingDelete")
        self.assertTrue(all(p.exists() for p in plan.files.values()))

    def test_notes_replace_only_when_given(self):
        _, kept = self.plan({"text"})
        _, replaced = self.plan({"text"}, notes="new notes")
        self.assertEqual(kept["notesForCertification"], "old notes")
        self.assertEqual(replaced["notesForCertification"], "new notes")

    def test_zip_holds_exactly_the_planned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(tmp)
            plan = push.Plan(self.store, published(), {"package", "screenshots", "art"}, ["en"], bundle, "1.0.7.0",
                             log=quiet())
            plan.build()
            with zipfile.ZipFile(push.write_zip(plan.files, Path(tmp) / "s.zip")) as z:
                self.assertEqual(sorted(z.namelist()), sorted(plan.files))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeOpener:
    """Records requests; answers sign-in with a token and the API from a dict of (method, path) -> body."""

    def __init__(self, answers=None):
        self.requests, self.answers = [], answers or {}

    def __call__(self, req):
        self.requests.append((req.get_method(), req.full_url, req.data))
        if "login.microsoftonline.com" in req.full_url:
            return Response(json.dumps({"access_token": "t"}).encode())
        path = req.full_url.removeprefix(push.API)
        answer = self.answers.get((req.get_method(), path))
        return Response(json.dumps(answer).encode() if answer is not None else b"")


class ClientTests(unittest.TestCase):
    def client(self, answers=None):
        with mock.patch.dict("os.environ", CREDENTIALS):
            return push.Client("x", opener=FakeOpener(answers))

    def test_signs_in_once_and_sends_the_token(self):
        client = self.client({("GET", "/applications/x"): {"id": "x"}})
        self.assertEqual(client.app(), {"id": "x"})
        client.app()
        logins = [r for r in client.open.requests if "login" in r[1]]
        self.assertEqual(len(logins), 1)
        self.assertIn(b"resource=https%3A%2F%2Fmanage.devcenter.microsoft.com", logins[0][2])

    def test_upload_sends_blocks_then_the_block_list(self):
        client = self.client()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(push, "BLOCK", 4):
            path = Path(tmp) / "s.zip"
            path.write_bytes(b"0123456789")
            client.upload("https://blob/x?sv=1&sig=s", path, log=quiet())
        puts = client.open.requests
        self.assertEqual([r[2] for r in puts[:3]], [b"0123", b"4567", b"89"])
        self.assertTrue(all("&comp=block&blockid=" in r[1] for r in puts[:3]))
        ids = [base64.b64encode(f"{i:08d}".encode()).decode() for i in range(3)]
        self.assertTrue(puts[3][1].endswith("&comp=blocklist"))
        self.assertEqual(puts[3][2].decode().count("<Latest>"), 3)
        self.assertIn(f"<Latest>{ids[2]}</Latest>", puts[3][2].decode())

    def test_credentials_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {}, clear=True):
            file = Path(tmp) / "credentials.json"
            file.write_text(json.dumps({"tenantId": "a", "clientId": "b", "clientSecret": "c"}), "utf8")
            with mock.patch.object(push, "CREDENTIALS", file):
                self.assertEqual(push.credentials(), ["a", "b", "c"])
            with mock.patch.object(push, "CREDENTIALS", Path(tmp) / "none.json"), self.assertRaises(SystemExit):
                push.credentials()


class PrepareTests(StoreTestCase):
    def run_prepare(self, apply, pending=None, replace=False):
        app_path = f"/applications/{self.store.app_id}"
        created = dict(published(), id="2", status="PendingCommit", fileUploadUrl="https://blob/x?sig=s")
        answers = {
            ("GET", app_path): {"pendingApplicationSubmission": {"id": pending} if pending else None,
                                "lastPublishedApplicationSubmission": {"id": "1"}},
            ("GET", f"{app_path}/submissions/1"): published(),
            ("POST", f"{app_path}/submissions"): created,
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", CREDENTIALS), \
                mock.patch("builtins.print"):
            client = push.Client(self.store.app_id, opener=FakeOpener(answers))
            args = argparse.Namespace(bundle=str(make_bundle(tmp)), only="package,text", notes=None,
                                      replace_pending=replace, apply=apply, langs=["en"])
            push.cmd_prepare(client, self.store, args)
        return [(m, u.removeprefix(push.API), d) for m, u, d in client.open.requests if "login" not in u]

    def test_dry_run_only_reads(self):
        calls = self.run_prepare(apply=False)
        self.assertEqual({m for m, _, _ in calls}, {"GET"})

    def test_apply_creates_uploads_and_saves_without_committing(self):
        calls = self.run_prepare(apply=True)
        methods = [(m, u.split("?")[0]) for m, u, _ in calls]
        self.assertIn(("POST", "/applications/9NFIXTURE001/submissions"), methods)
        self.assertTrue(any(u.startswith("https://blob/x") for _, u in methods))
        put = next(d for m, u, d in calls if m == "PUT" and u.endswith("/submissions/2"))
        saved = json.loads(put)
        self.assertEqual(saved["applicationPackages"][-1]["fileStatus"], "PendingUpload")
        self.assertFalse(any(u.endswith("/commit") for _, u in methods))

    def test_refuses_a_pending_submission_unless_asked(self):
        with self.assertRaises(SystemExit):
            self.run_prepare(apply=True, pending="7")
        calls = self.run_prepare(apply=True, pending="7", replace=True)
        self.assertIn(("DELETE", "/applications/9NFIXTURE001/submissions/7"), [(m, u) for m, u, _ in calls])

    def test_main_needs_a_store_id(self):
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        del data["store"]["microsoft"]["storeId"]
        path.write_text(json.dumps(data), "utf8")
        with self.assertRaises(SystemExit) as stop:
            push.main(["status"], client=lambda _: self.fail("no client without a Store ID"))
        self.assertIn("storeId", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
