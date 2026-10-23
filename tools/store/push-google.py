#!/usr/bin/env python3
"""Pushes the Google Play store listing and one-time products through the Play Developer API (androidpublisher v3).

Usage (from the app's root):
  python3 laching/tools/store/push-google.py status
  python3 laching/tools/store/push-google.py prepare [--only text,graphics,products] [--apply] [--not-sent-for-review]
                                                     [lang ...]

prepare  without --apply prints what it would change: against Google Play when the credentials are there, against the
         local files alone when they aren't. With --apply it writes, per part in --only (default all three):
           text      each language's title, short and full description (`google.title`, `.short`, `.full` from
                     `store.dir`/<lang>.md), added where Google Play doesn't have the language yet
           graphics  the source language's icon (`store.google.icon`, google/icon-512.png) and feature graphic
                     (`.featureGraphic`, google/feature-graphic.png), and per language that has a folder the phone
                     screenshots (`.phoneScreenshots`/<lang>/*.png, google/phone) and the tablet ones
                     (`.tabletScreenshots`/<lang>/*.png, google/tablet: both the 7-inch and the 10-inch slot), in file
                     order; a set whose files all match is left alone, and a language without a folder shows the
                     source language's
           products  `store.apple.iaps` as one-time products with the same IDs: the name and description from the
                     `google.iap.*` fields, one buy option priced at the product's `price` in USD, converted to every
                     region by Google Play; created when missing, then activated
         Text and graphics go through one edit, committed at the end (--not-sent-for-review when Google Play asks for
         it: the changes then wait for Publishing overview → Send for review). Products are written directly.
status   prints the languages, graphics and products Google Play has.

The app is `store.google.packageName`; `store.google.locales` maps a listing language to Google Play's code, falling
back to `store.apple.locales` and then the language itself (pt-BR, ro). Standard library and the `openssl` command
only (it signs the service account's token request).

Credentials: a Google Cloud service account's JSON key, the account invited in Play Console → Users and permissions
with access to the app. Put the key in ~/.googleplay/credentials.json, or point GOOGLE_PLAY_CREDENTIALS at it. Never
in a repository. One service account can serve every app of the developer account. Step by step:
google-api-setup.md next to this. The API refuses an app until Play Console has had one app bundle uploaded by hand.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from listing import GOOGLE_LIMITS, Listing, google_field, load  # noqa: E402

API = "https://androidpublisher.googleapis.com/androidpublisher/v3"
UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"
CREDENTIALS = Path.home() / ".googleplay" / "credentials.json"
PARTS = ("text", "graphics", "products")
TEXT = {"title": "google.title", "shortDescription": "google.short", "fullDescription": "google.full"}
IMAGES = ("icon", "featureGraphic", "phoneScreenshots", "sevenInchScreenshots", "tenInchScreenshots")
OPTION = "buy"


class ApiError(Exception):
    pass


def credentials() -> dict | None:
    """The service account's JSON key, from GOOGLE_PLAY_CREDENTIALS or ~/.googleplay/credentials.json; None without."""
    path = Path(os.environ.get("GOOGLE_PLAY_CREDENTIALS") or CREDENTIALS).expanduser()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def assertion(key: dict, now: int | None = None) -> str:
    """The JWT a service account trades for an access token, signed RS256 with its private key through openssl."""
    now = int(time.time()) if now is None else now
    claims = {"iss": key["client_email"], "scope": SCOPE, "aud": key["token_uri"], "iat": now, "exp": now + 3600}
    signing = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    fd, pem = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as file:
            file.write(key["private_key"])
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", pem], input=signing.encode(),
                                   capture_output=True, check=True).stdout
    finally:
        os.unlink(pem)
    return signing + "." + b64(signature)


class Client:
    """The Play Developer API for one app, authorized as the service account."""

    def __init__(self, package: str, key: dict, opener=urllib.request.urlopen):
        self.package, self.open = package, opener
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": assertion(key)}).encode()
        self.token = self.send(urllib.request.Request(key["token_uri"], body))["access_token"]

    def send(self, request):
        try:
            with self.open(request) as response:
                text = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(f"{request.get_method()} {request.full_url}: {error.code} "
                           f"{error.read().decode(errors='replace')}") from None
        return json.loads(text) if text else {}

    def call(self, method: str, path: str, body=None, query=None, data: bytes | None = None, base=API):
        url = f"{base}/applications/{self.package}/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "image/png"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        return self.send(urllib.request.Request(url, data, headers, method=method))


def locale(store: Listing, lang: str) -> str:
    return store.google_locale.get(lang, lang)


def pngs(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.png")) if folder.is_dir() else []


def graphics(store: Listing, lang: str) -> dict[str, list[Path]]:
    """Image type -> a language's local files, leaving out the types it has none of (the source language's show)."""
    phone, tablet = pngs(store.phone_screenshots / lang), pngs(store.tablet_screenshots / lang)
    sets = {"phoneScreenshots": phone, "sevenInchScreenshots": tablet, "tenInchScreenshots": tablet}
    if lang == store.source:
        sets = {"icon": [store.google_icon], "featureGraphic": [store.feature_graphic], **sets}
    return {kind: [file for file in files if file.exists()] for kind, files in sets.items()
            if any(file.exists() for file in files)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def money(price: str) -> dict:
    """A decimal USD price as the API's Money: "0.99" -> units "0", nanos 990000000."""
    units, _, fraction = price.partition(".")
    return {"currencyCode": "USD", "units": units, "nanos": int((fraction + "000000000")[:9])}


def products(store: Listing) -> dict[str, dict]:
    """Product ID -> its listings (the languages with both fields) and USD price."""
    result = {}
    for product, spec in store.iaps.items():
        listings = []
        for lang in store.langs:
            fields = store.fields(lang)
            name, description = fields.get(google_field(spec["name"])), fields.get(google_field(spec["description"]))
            if name and description:
                listings.append({"languageCode": locale(store, lang), "title": name, "description": description})
        result[product] = {"listings": listings, "price": spec["price"]}
    return result


def cmd_status(api: Client, store: Listing, args) -> None:
    edit = api.call("POST", "edits", {})["id"]
    try:
        for listing in api.call("GET", f"edits/{edit}/listings").get("listings", []):
            language = listing["language"]
            counts = {kind: len(api.call("GET", f"edits/{edit}/listings/{language}/{kind}").get("images", []))
                      for kind in IMAGES}
            print(f"{language:8} {listing.get('title', '')!r}  " + "  ".join(f"{k} {v}" for k, v in counts.items() if v))
    finally:
        api.call("DELETE", f"edits/{edit}")
    for product in api.call("GET", "oneTimeProducts").get("oneTimeProducts", []):
        options = ", ".join(f"{o['purchaseOptionId']} {o.get('state', '?')}" for o in product.get("purchaseOptions", []))
        print(f"{product['productId']}: {len(product.get('listings', []))} languages, {options or 'no purchase option'}")


def prepare_text(api: Client | None, edit: str | None, store: Listing, langs, apply: bool) -> None:
    remote = {}
    if api:
        remote = {item["language"]: item for item in api.call("GET", f"edits/{edit}/listings").get("listings", [])}
    for lang in langs:
        fields, code = store.fields(lang), locale(store, lang)
        body = {"language": code, **{key: fields[field] for key, field in TEXT.items() if fields.get(field)}}
        for key, field in TEXT.items():
            if len(body.get(key, "")) > GOOGLE_LIMITS[field]:
                sys.exit(f"push-google: {lang}: {field} is over {GOOGLE_LIMITS[field]} characters")
        current = remote.get(code)
        changed = [key for key in TEXT if body.get(key) != (current or {}).get(key)] if api else list(TEXT)
        if not changed:
            continue
        print(f"text {code}: {'add' if api and not current else 'update'} {', '.join(changed)}")
        if apply:
            api.call("PUT", f"edits/{edit}/listings/{code}", body)


def prepare_graphics(api: Client | None, edit: str | None, store: Listing, langs, apply: bool) -> None:
    for lang in langs:
        code = locale(store, lang)
        for kind, files in graphics(store, lang).items():
            if api:
                images = api.call("GET", f"edits/{edit}/listings/{code}/{kind}").get("images", [])
                if [image.get("sha256") for image in images] == [sha256(file) for file in files]:
                    continue
            print(f"graphics {code} {kind}: {', '.join(file.name for file in files)}")
            if apply:
                api.call("DELETE", f"edits/{edit}/listings/{code}/{kind}")
                for file in files:
                    api.call("POST", f"edits/{edit}/listings/{code}/{kind}", query={"uploadType": "media"},
                             data=file.read_bytes(), base=UPLOAD)


def prepare_products(api: Client | None, store: Listing, apply: bool) -> None:
    remote = {}
    if api:
        remote = {item["productId"]: item for item in api.call("GET", "oneTimeProducts").get("oneTimeProducts", [])}
    for product, want in products(store).items():
        current = remote.get(product)
        print(f"product {product}: {'update' if current else 'create'}, {len(want['listings'])} languages,"
              f" USD {want['price']}")
        if not apply:
            continue
        converted = api.call("POST", "pricing:convertRegionPrices", {"price": money(want["price"])})
        regions = [{"regionCode": region, "price": price["price"], "availability": "AVAILABLE"}
                   for region, price in converted["convertedRegionPrices"].items()]
        option = {"purchaseOptionId": OPTION, "buyOption": {"legacyCompatible": True},
                  "regionalPricingAndAvailabilityConfigs": regions}
        body = {"packageName": api.package, "productId": product, "listings": want["listings"],
                "purchaseOptions": [option]}
        api.call("PATCH", f"onetimeproducts/{product}", body,
                 {"allowMissing": "true", "updateMask": "listings,purchaseOptions",
                  "regionsVersion.version": converted["regionVersion"]["version"]})
        state = next((o.get("state") for o in (current or {}).get("purchaseOptions", [])
                      if o["purchaseOptionId"] == OPTION), None)
        if state != "ACTIVE":
            api.call("POST", f"oneTimeProducts/{product}/purchaseOptions:batchUpdateStates", {"requests": [
                {"activatePurchaseOptionRequest": {"packageName": api.package, "productId": product,
                                                   "purchaseOptionId": OPTION}}]})


def cmd_prepare(api: Client | None, store: Listing, args) -> None:
    parts = args.only.split(",")
    if api is None:
        print("no credentials: listing the local files, not comparing with Google Play")
    if "text" in parts or "graphics" in parts:
        edit = api.call("POST", "edits", {})["id"] if api else None
        try:
            if "text" in parts:
                prepare_text(api, edit, store, args.langs, args.apply)
            if "graphics" in parts:
                prepare_graphics(api, edit, store, args.langs, args.apply)
            if args.apply:
                query = {"changesNotSentForReview": "true"} if args.not_sent_for_review else None
                api.call("POST", f"edits/{edit}:commit", query=query)
                print(f"committed edit {edit}")
                edit = None
        finally:
            if api and edit:
                api.call("DELETE", f"edits/{edit}")
    if "products" in parts:
        prepare_products(api, store, args.apply)


def main(argv=None, client=Client) -> int:
    store = load()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="the languages, graphics and products Google Play has")
    prepare = commands.add_parser("prepare", help="write the listing and products (dry run without --apply)")
    prepare.add_argument("--only", default=",".join(PARTS), help=f"comma-separated: {', '.join(PARTS)}")
    prepare.add_argument("--apply", action="store_true", help="write and commit (default: dry run)")
    prepare.add_argument("--not-sent-for-review", action="store_true",
                         help="commit with changesNotSentForReview (when Google Play asks for it)")
    prepare.add_argument("langs", nargs="*", default=store.langs)
    args = parser.parse_args(argv)
    if args.command == "prepare" and (set(args.only.split(",")) - set(PARTS) or not set(args.langs) <= set(store.langs)):
        parser.error(f"--only takes {', '.join(PARTS)}; languages are {' '.join(store.langs)}")
    if store.google is None or not store.package:
        sys.exit("push-google: companion.json has no store.google.packageName (the Android app's applicationId)")
    key = credentials()
    if key is None and (args.command == "status" or args.apply):
        sys.exit(f"push-google: no credentials: write the service account's JSON key to {CREDENTIALS} or point "
                 "GOOGLE_PLAY_CREDENTIALS at it (google-api-setup.md)")
    try:
        api = client(store.package, key) if key else None
        {"status": cmd_status, "prepare": cmd_prepare}[args.command](api, store, args)
    except ApiError as error:
        sys.exit(f"push-google: {error}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 on a cp1252 Windows console too (companion.utf8_console)
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
