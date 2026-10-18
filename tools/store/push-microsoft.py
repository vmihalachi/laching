#!/usr/bin/env python3
"""Pushes a Windows release to the Microsoft Store through the Partner Center submission API (push.py is App Store
Connect's).

Usage (from the app's root):
  python3 laching/tools/store/push-microsoft.py status
  python3 laching/tools/store/push-microsoft.py prepare --bundle <.msixbundle> [--only package,text,screenshots,art]
                                                        [--notes FILE] [--replace-pending] [--apply] [lang ...]
  python3 laching/tools/store/push-microsoft.py commit [--no-wait]

prepare  starts a submission (Partner Center copies the last published one: pricing, audience, age rating and
         properties stay), then changes only the parts named in --only (default package,text):
           package      every old package goes, the bundle comes in (its version must be higher)
           text         title, description, short description, features, search terms, copyright, developer
                        (`store.microsoft.publisher`), and each screenshot's caption, from `store.dir`/<lang>.md
           screenshots  `store.microsoft.screenshots`/<lang>/*.png replace the listing's screenshots (opt-in:
                        store-screenshots.py --canvas microsoft --lang all first)
           art          `store.microsoft.art`/*.png replace the poster, box and super hero art (opt-in)
         --notes FILE replaces the notes for certification. Without --apply it reads the last published submission
         and prints what would change. With --apply it creates the submission, uploads one zip and saves the data, but
         doesn't commit: look at it in Partner Center (don't edit it there, or the API can't touch it again), then run
         commit.
commit   sends the pending submission to certification and waits for PreProcessing or CommitFailed.
status   prints the pending and the last published submission, with errors and certification reports.

The app is `store.microsoft.storeId` (its Store ID); each language wants as many screenshots and captions as
`store.microsoft.shots` names. Every package in the bundle must be a Native AOT build holding its executable.

Credentials: a Microsoft Entra application added in Partner Center (Account settings > User management > Microsoft
Entra applications) with the Manager role; its tenant ID, client ID and a key. Put them in MS_STORE_TENANT_ID,
MS_STORE_CLIENT_ID and MS_STORE_CLIENT_SECRET (the repository secrets of the windows-release workflow), or in
~/.partnercenter/credentials.json as {"tenantId": ..., "clientId": ..., "clientSecret": ...}. Never in a repository.
One application serves every app of the Partner Center account. Step by step: microsoft-api-setup.md next to this.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from listing import Listing, load  # noqa: E402

API = "https://manage.devcenter.microsoft.com/v1.0/my"
ART = {  # submission API image type -> file in art/ (import.py's three slots)
    "StoreLogo9x16": "poster-art.png",  # Partner Center's 2:3 poster art
    "StoreLogoSquare": "box-art.png",  # 1:1 box art
    "PromotionalArt16x9": "super-hero-art.png",  # 16:9 super hero art
}
PARTS = ("package", "text", "screenshots", "art")
BLOCK = 4 * 1024 * 1024  # Azure Put Block size the oldest SAS versions accept
CREDENTIALS = Path.home() / ".partnercenter" / "credentials.json"


def lines(fields, key):
    value = fields.get(key, "")
    return value.split("\n") if value else []


def version_key(version):
    return tuple(int(x) for x in version.split("."))


class Store:
    """`store.microsoft` of the app: what a submission is made of."""

    def __init__(self, listing: Listing):
        microsoft = listing.microsoft
        if microsoft is None:
            sys.exit("push-microsoft: companion.json has no store.microsoft")
        self.listing = listing
        self.app_id = microsoft.get("storeId")
        self.publisher = microsoft["publisher"]
        self.count = len(microsoft["shots"])
        self.screenshots = listing.dir / microsoft.get("screenshots", "microsoft/screenshots")
        self.art = listing.dir / microsoft.get("art", "microsoft/art")
        self.langs = listing.langs

    def fields(self, lang):
        return self.listing.fields(lang)


# --- the bundle ------------------------------------------------------------------------------------


def bundle_info(path):
    """The bundle's version and packages, and why it can't go to the Store: a package that isn't a Native AOT build."""
    with zipfile.ZipFile(path) as bundle:
        manifest = bundle.read("AppxMetadata/AppxBundleManifest.xml").decode("utf-8")
        match = re.search(r"<Identity\b[^>]*\bVersion=\"([0-9.]+)\"", manifest)
        if not match:
            raise ValueError(f"{path.name}: no Identity Version in AppxBundleManifest.xml")
        problems, packages = [], [n for n in bundle.namelist() if n.lower().endswith(".msix")]
        if not packages:
            problems.append(f"{path.name} holds no .msix")
        for name in packages:
            with bundle.open(name) as raw, tempfile.TemporaryFile() as copy:
                copy.write(raw.read())
                copy.seek(0)
                with zipfile.ZipFile(copy) as package:
                    names = [urllib.parse.unquote(n) for n in package.namelist()]
                    executable = re.search(r'<Application\b[^>]*\bExecutable="([^"]+)"',
                                           package.read("AppxManifest.xml").decode("utf-8")) if "AppxManifest.xml" in names else None
            if not executable:
                problems.append(f"{name} has no application in its AppxManifest.xml")
            elif executable.group(1) not in names:
                problems.append(f"{name} has no {executable.group(1)}")
            if any(n.endswith(".runtimeconfig.json") for n in names):
                problems.append(f"{name} expects an installed .NET runtime (not a Native AOT build)")
    return match.group(1), packages, problems


# --- planning: pure, so the tests can run it without Partner Center -----------------------------------


class Plan:
    """Turns a submission (as the API returns it) into the one to save, plus the files the zip needs."""

    def __init__(self, store, submission, parts, langs, bundle=None, bundle_version=None, notes=None, log=None):
        self.store = store
        self.sub = json.loads(json.dumps(submission))  # a deep copy
        self.parts, self.langs = parts, langs
        self.log = log or (lambda *a: print(*a))
        self.bundle, self.bundle_version, self.notes = bundle, bundle_version, notes
        self.files = {}  # name in the zip -> local path
        self.problems = []

    def build(self):
        if "package" in self.parts:
            self.package()
        if self.parts & {"text", "screenshots", "art"}:
            self.listings()
        if self.notes is not None and self.notes != (self.sub.get("notesForCertification") or ""):
            self.log("Notes for certification: replace")
            self.sub["notesForCertification"] = self.notes
        return self.sub

    def package(self):
        packages = self.sub.setdefault("applicationPackages", [])
        live = [p for p in packages if p.get("fileStatus") != "PendingDelete"]
        newest = max((p.get("version") or "0" for p in live), key=version_key, default="0")
        if version_key(self.bundle_version) <= version_key(newest):
            self.problems.append(f"the bundle is {self.bundle_version}, but {newest} is already in the Store: "
                                 "bump Package.appxmanifest")
        for p in live:
            self.log(f"Package: remove {p.get('fileName')} ({p.get('version')}, {p.get('architecture')})")
            p["fileStatus"] = "PendingDelete"
        self.log(f"Package: add {self.bundle.name} ({self.bundle_version})")
        packages.append({"fileName": self.bundle.name, "fileStatus": "PendingUpload",
                         "minimumDirectXVersion": "None", "minimumSystemRam": "None"})
        self.files[self.bundle.name] = self.bundle

    def listing_key(self, lang):
        """The submission's own key for a language (Partner Center lowercases them), else a new one."""
        listings = self.sub.setdefault("listings", {})
        return next((k for k in listings if k.lower() == lang.lower()), lang.lower())

    def listings(self):
        count = self.store.count
        for lang in self.langs:
            key = self.listing_key(lang)
            listings = self.sub["listings"]
            if key not in listings:
                self.log(f"{key}: add the language")
                listings[key] = {"baseListing": {"images": []}, "platformOverrides": {}}
            base = listings[key].setdefault("baseListing", {})
            base.setdefault("images", [])
            fields = self.store.fields(lang)
            captions = lines(fields, "ms.captions")
            if "text" in self.parts or "screenshots" in self.parts:
                if len(captions) != count:
                    self.problems.append(f"{lang}: {len(captions)} captions in ms.captions, want {count}")
            if "text" in self.parts:
                self.text(key, base, fields)
            if "screenshots" in self.parts:
                self.screenshots(lang, key, base, captions)
            elif "text" in self.parts:
                self.captions(key, base, captions)
            if "art" in self.parts:
                self.art(key, base)

    def text(self, key, base, fields):
        want = {
            "title": fields["ms.name"],
            "description": fields["ms.description"],
            "shortDescription": fields.get("ms.short", ""),
            "features": lines(fields, "ms.features"),
            "keywords": lines(fields, "ms.search"),
            "copyrightAndTrademarkInfo": fields["ms.copyright"],
            "devStudio": self.store.publisher,
        }
        diff = [k for k, v in want.items() if (base.get(k) or ([] if isinstance(v, list) else "")) != v]
        if diff:
            self.log(f"{key}: update {', '.join(diff)}")
            base.update(want)

    def live_images(self, base, kinds):
        return [i for i in base["images"] if i.get("imageType") in kinds and i.get("fileStatus") != "PendingDelete"]

    def captions(self, key, base, captions):
        """Captions live on the screenshots, so text alone can retitle the ones already uploaded."""
        shots = self.live_images(base, {"Screenshot"})
        if len(shots) != len(captions):
            if shots:
                self.log(f"{key}: {len(shots)} screenshots but {len(captions)} captions: "
                         "captions left alone (push the screenshots too)")
            return
        changed = [i for i, (shot, caption) in enumerate(zip(shots, captions)) if shot.get("description") != caption]
        if changed:
            self.log(f"{key}: update captions {', '.join(str(i + 1) for i in changed)}")
            for shot, caption in zip(shots, captions):
                shot["description"] = caption

    def replace(self, key, base, kinds, new, label):
        old = self.live_images(base, kinds)
        self.log(f"{key}: {label}: replace {len(old)} with {len(new)}")
        for image in old:
            image["fileStatus"] = "PendingDelete"
        base["images"].extend(new)

    def screenshots(self, lang, key, base, captions):
        count = self.store.count
        shots = sorted((self.store.screenshots / lang).glob("*.png"))
        if len(shots) != count:
            self.problems.append(f"{lang}: {len(shots)} screenshots in {self.store.screenshots / lang}, want {count} "
                                 f"(store-screenshots.py --canvas microsoft --lang {lang})")
            return
        new = []
        for i, shot in enumerate(shots):
            name = f"{lang}-{shot.name}"  # flat names: no path separators to get wrong
            self.files[name] = shot
            new.append({"fileName": name, "fileStatus": "PendingUpload", "imageType": "Screenshot",
                        "description": captions[i] if i < len(captions) else ""})
        self.replace(key, base, {"Screenshot"}, new, "screenshots")

    def art(self, key, base):
        new = []
        for kind, file in ART.items():
            name = f"art-{file}"
            self.files[name] = self.store.art / file
            new.append({"fileName": name, "fileStatus": "PendingUpload", "imageType": kind, "description": ""})
        self.replace(key, base, set(ART), new, "art")


def write_zip(files, path):
    """PNGs and bundles are compressed already: stored, so the zip is quick to write."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for name, source in files.items():
            z.write(source, name)
    return path


# --- API client -------------------------------------------------------------------------------------


class ApiError(Exception):
    pass


def credentials():
    env = [os.environ.get(k) for k in ("MS_STORE_TENANT_ID", "MS_STORE_CLIENT_ID", "MS_STORE_CLIENT_SECRET")]
    if all(env):
        return env
    if CREDENTIALS.exists():
        data = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        values = [data.get(k) for k in ("tenantId", "clientId", "clientSecret")]
        if all(values):
            return values
    sys.exit("No Partner Center credentials: set MS_STORE_TENANT_ID, MS_STORE_CLIENT_ID and "
             f"MS_STORE_CLIENT_SECRET, or write {CREDENTIALS} (microsoft-api-setup.md).")


class Client:
    def __init__(self, app_id, opener=urllib.request.urlopen):
        self.app_id = app_id
        self.tenant, self.client_id, self.secret = credentials()
        self.open = opener
        self.token, self.minted = None, 0

    def authorize(self):
        if self.token and time.time() - self.minted < 50 * 60:  # tokens last 60 minutes
            return
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                       "client_secret": self.secret,
                                       "resource": "https://manage.devcenter.microsoft.com"}).encode()
        req = urllib.request.Request(f"https://login.microsoftonline.com/{self.tenant}/oauth2/token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.open(req) as response:
                self.token, self.minted = json.loads(response.read())["access_token"], time.time()
        except urllib.error.HTTPError as e:
            raise ApiError(f"sign-in: {e.code} {e.read().decode(errors='replace')}") from None

    def request(self, method, path, body=None):
        self.authorize()
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
            try:
                with self.open(req) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(f"{method} {path}: {e.code} {e.read().decode(errors='replace')}") from None

    def app(self):
        return self.request("GET", f"/applications/{self.app_id}")

    def submission(self, sid):
        return self.request("GET", f"/applications/{self.app_id}/submissions/{sid}")

    def upload(self, url, path, log=print):
        """Put Block + Put Block List to the submission's SAS URL (one Put Blob caps at 64 MB on old SAS)."""
        size, ids = path.stat().st_size, []
        with open(path, "rb") as f:
            while chunk := f.read(BLOCK):
                block_id = base64.b64encode(f"{len(ids):08d}".encode()).decode()
                self.put(f"{url}&comp=block&blockid={urllib.parse.quote(block_id)}", chunk)
                ids.append(block_id)
                if len(ids) % 16 == 0:
                    log(f"  uploaded {min(len(ids) * BLOCK, size) // 2**20} of {size // 2**20} MB")
        body = ('<?xml version="1.0" encoding="utf-8"?><BlockList>'
                + "".join(f"<Latest>{i}</Latest>" for i in ids) + "</BlockList>").encode()
        self.put(f"{url}&comp=blocklist", body)

    def put(self, url, data):
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, method="PUT", headers={"Content-Length": str(len(data))})
            try:
                with self.open(req):
                    return
            except urllib.error.HTTPError as e:
                if e.code in (500, 503) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(f"upload: {e.code} {e.read().decode(errors='replace')}") from None


# --- commands ---------------------------------------------------------------------------------------


def describe(sub, label):
    packages = [f"{p.get('version')} {p.get('architecture')}" for p in sub.get("applicationPackages", [])
                if p.get("fileStatus") != "PendingDelete"]
    print(f"{label}: {sub.get('friendlyName') or sub['id']} ({sub['id']}), {sub.get('status')}")
    print(f"  packages: {', '.join(packages) or 'none'}")
    print(f"  visibility {sub.get('visibility')}, publish {sub.get('targetPublishMode')}, "
          f"price {(sub.get('pricing') or {}).get('priceId')}, {len(sub.get('listings') or {})} listings")
    details = sub.get("statusDetails") or {}
    for kind in ("errors", "warnings"):
        for item in details.get(kind) or []:
            print(f"  {kind[:-1]}: {item.get('code')}: {item.get('details')}")
    for report in details.get("certificationReports") or []:
        print(f"  certification report {report.get('date')}: {report.get('reportUrl')}")


def cmd_status(api, store, args):
    info = api.app()
    pending = (info.get("pendingApplicationSubmission") or {}).get("id")
    published = (info.get("lastPublishedApplicationSubmission") or {}).get("id")
    if pending:
        describe(api.submission(pending), "Pending")
    else:
        print("Pending: none")
    if published:
        describe(api.submission(published), "Last published")


def cmd_prepare(api, store, args):
    parts = set(args.only.split(","))
    bundle = Path(args.bundle) if args.bundle else None
    if "package" in parts:
        if not bundle or not bundle.exists():
            sys.exit("--bundle <.msixbundle> is required for the package part")
        bundle_version, packages, problems = bundle_info(bundle)
        if problems:
            sys.exit("\n".join(problems))
        print(f"Bundle {bundle.name}: {bundle_version}, {', '.join(Path(p).name for p in packages)}")
    else:
        bundle_version = None
    check = subprocess.run([sys.executable, str(HERE / "check.py"), *args.langs], capture_output=True, text=True,
                           env={**os.environ, "COMPANION_ROOT": str(store.listing.config.root)})
    if check.returncode:
        sys.exit(check.stdout + check.stderr + "Fix the listing first (laching/tools/store/check.py).")
    notes = Path(args.notes).read_text(encoding="utf-8").strip() if args.notes else None

    info = api.app()
    pending = (info.get("pendingApplicationSubmission") or {}).get("id")
    published = (info.get("lastPublishedApplicationSubmission") or {}).get("id")
    if pending and not args.replace_pending:
        sys.exit(f"Submission {pending} is already in progress. Commit it (commit), finish it in Partner "
                 "Center, or pass --replace-pending to delete it and start over.")
    if not published:
        sys.exit("The app has no published submission: make the first one in Partner Center (with its age rating), "
                 "then the API can make the next ones.")
    # A new submission copies the last published one, so planning on that finds every problem first.
    base = api.submission(published)
    if not args.apply:
        print("Dry run: nothing is written. Add --apply to create the submission.\n")
        if pending:
            print(f"Would delete the pending submission {pending}.")
        print(f"Starting from the last published submission, {base.get('friendlyName') or base['id']}:")
    plan = Plan(store, base, parts, args.langs, bundle, bundle_version, notes,
                log=None if not args.apply else lambda *_: None)
    updated = plan.build()
    if plan.problems:
        sys.exit("\nProblems:\n" + "\n".join(f"  {p}" for p in plan.problems))
    if args.apply:
        if pending:
            print(f"Deleting the pending submission {pending}")
            api.request("DELETE", f"/applications/{store.app_id}/submissions/{pending}")
        base = api.request("POST", f"/applications/{store.app_id}/submissions")
        print(f"Created {base.get('friendlyName') or base['id']} ({base['id']})")
        plan = Plan(store, base, parts, args.langs, bundle, bundle_version, notes)
        updated = plan.build()
    print(f"Visibility {updated.get('visibility')}, publish {updated.get('targetPublishMode')} (unchanged)")
    if not args.apply:
        size = sum(p.stat().st_size for p in plan.files.values())
        print(f"\nDry run done: would upload {len(plan.files)} files ({size // 2**20} MB) and save the submission.")
        return
    if plan.files:
        with tempfile.TemporaryDirectory() as tmp:
            archive = write_zip(plan.files, Path(tmp) / "submission.zip")
            print(f"Uploading {len(plan.files)} files ({archive.stat().st_size // 2**20} MB)")
            api.upload(base["fileUploadUrl"], archive)
    api.request("PUT", f"/applications/{store.app_id}/submissions/{base['id']}", updated)
    print("Saved. Look at it in Partner Center without editing it, then: "
          "python3 laching/tools/store/push-microsoft.py commit")


def cmd_commit(api, store, args):
    pending = (api.app().get("pendingApplicationSubmission") or {}).get("id")
    if not pending:
        sys.exit("No submission in progress (prepare --apply makes one).")
    api.request("POST", f"/applications/{store.app_id}/submissions/{pending}/commit")
    print(f"Committed {pending}.")
    if args.no_wait:
        return
    status = None
    for _ in range(60):
        status = api.request("GET", f"/applications/{store.app_id}/submissions/{pending}/status")
        if status.get("status") != "CommitStarted":
            break
        time.sleep(10)
    print(f"Status: {status.get('status')}")
    for kind in ("errors", "warnings"):
        for item in (status.get("statusDetails") or {}).get(kind) or []:
            print(f"  {kind[:-1]}: {item.get('code')}: {item.get('details')}")
    if status.get("status") == "CommitFailed":
        sys.exit(1)


def main(argv=None, client=Client):
    store = Store(load())
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="the pending and last published submissions")
    prepare = sub.add_parser("prepare", help="create and fill a submission (dry run without --apply)")
    prepare.add_argument("--bundle", help="the .msixbundle to upload")
    prepare.add_argument("--only", default="package,text", help=f"comma-separated: {', '.join(PARTS)}")
    prepare.add_argument("--notes", help="a text file with the notes for certification")
    prepare.add_argument("--replace-pending", action="store_true", help="delete a submission in progress first")
    prepare.add_argument("--apply", action="store_true", help="create, upload and save (default: dry run)")
    prepare.add_argument("langs", nargs="*", default=store.langs)
    commit = sub.add_parser("commit", help="send the pending submission to certification")
    commit.add_argument("--no-wait", action="store_true", help="don't wait for PreProcessing or CommitFailed")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        parts = set(args.only.split(","))
        if parts - set(PARTS) or not set(args.langs) <= set(store.langs):
            parser.error(f"--only takes {', '.join(PARTS)}; languages are {' '.join(store.langs)}")
    if not store.app_id:
        sys.exit("push-microsoft: companion.json has no store.microsoft.storeId (Partner Center > the app > Product "
                 "identity > Store ID)")
    try:
        {"status": cmd_status, "prepare": cmd_prepare, "commit": cmd_commit}[args.command](client(store.app_id), store, args)
    except ApiError as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
