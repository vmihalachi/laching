#!/usr/bin/env python3
"""Pushes the store listing (`store.dir`/<lang>.md) and the Apple uploads to App Store Connect, through its API.

Usage (from the app's root):
  python3 laching/tools/store/push.py --version 1.0 --status        # read only: what App Store Connect has, what's missing
  python3 laching/tools/store/push.py --version 1.0 [--create] [--build 4] [--only text,iap,screenshots] [lang ...]
  ... --apply        to write; without it nothing changes and the script prints what it would do.

What it syncs, per language (companion.json's `store.apple` names the app, its locales and in-app purchases):
  text         App Information (name, subtitle, privacy URL) and the version page (description, keywords,
               promotional text, What's New, support and marketing URLs), plus copyright and the release type
               (`store.apple.releaseType`, MANUAL or AFTER_APPROVAL: set on an editable version when configured, and
               manual on a version it creates without it)
  iap          each in-app purchase's display name, description, review note and review screenshot (each left alone
               when its config has none); one that doesn't exist yet is created with its price and availability
               (`store.apple.iaps`)
  screenshots  `store.apple.screenshots`/<lang>/*.png, in order; a set whose files all match is left alone
  review       the App Review notes, from the block under "App Review Information → Notes" in `store.apple.review.notes`
               (the contact details are left to App Store Connect; without the file, the notes are left alone)
  previews     `store.apple.previews`/preview-<lang>.mp4 (opt-in: --only ...,previews)
With `store.apple.ios` (a universal app), the iPhone and iPad version page too (on by default then):
  ios-text         the iOS version's page from the listing's `ios.*` fields (App Information is shared with the Mac)
  ios-screenshots  the iPhone 6.9" and iPad 13" sets, from `store.apple.ios.iphoneScreenshots`/<lang>/*.png and
                   `ios.ipadScreenshots`/<lang>/*.png, and the iPhone Duo's from `ios.duoScreenshots`/<lang>/*.png
                   (apple/duo by default; 1398x2034 or 2007x2853) when that folder exists: App Store Connect has no
                   screenshot set for the Duo, so they go through the App Asset Library, placed in file order
  ios-header       the iOS 27 product page header (`store.apple.ios.header`, apple/header.png by default: 3840x1646, no
                   alpha), one image placed on every language, in each placement group the App Asset Library's
                   reference data gives that size; on by default when the file exists
  ios-search       the iOS 27 search results asset (`store.apple.ios.searchResults`, apple/search-results.png by
                   default: 1920x1280 to 3840x2560, or 5244x2950, no alpha), placed like the header; on by default
                   when the file exists (without one, App Store search shows the screenshots)
The iOS version for --version is found or created (--create) like the Mac's, and --build is attached to it too; the
`review` part sets its App Review notes as well, and --status reports it (screenshots per display type) after the Mac's.
A field the listing doesn't have is left alone, and What's New is left out on a platform's first version (App Store
Connect refuses it there). Languages missing from App Store Connect
are added (App Information first: App Store Connect then adds them to the version, which is filled in rather than added
twice). The extra locales (`store.apple.extraLocales`: en-GB, es-MX, fr-CA, ...) are updated when they exist;
--extra-locales adds them too.

Credentials: an App Store Connect API key (Users and Access → Integrations → App Store Connect API →
Team Keys, access App Manager). Put the downloaded AuthKey_<KEY_ID>.p8 in
~/.appstoreconnect/private_keys/ (or point ASC_KEY_PATH at it) and export ASC_KEY_ID and ASC_ISSUER_ID.
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from listing import Listing, load  # noqa: E402

API = "https://api.appstoreconnect.apple.com"
EDITABLE = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED",
            "INVALID_BINARY"}
PARTS = ("text", "iap", "screenshots", "review", "previews", "ios-text", "ios-screenshots", "ios-header", "ios-search")
IPHONE_SCREENSHOTS = "APP_IPHONE_67"  # the 6.9" display (App Store Connect's name predates it)
IPAD_SCREENSHOTS = "APP_IPAD_PRO_3GEN_129"  # the 13" display
SCREENSHOT_NAMES = {IPHONE_SCREENSHOTS: 'iPhone 6.9"', IPAD_SCREENSHOTS: 'iPad 13"'}
# The App Asset Library's placement types: the product page header, the search results asset, and screenshots (the
# iPhone Duo's are only there, in its placement group).
HEADER = "PRODUCT_PAGE_HEADER_ASSET"
SEARCH = "APP_STORE_SEARCH_RESULTS_ASSET"
SCREENSHOT = "APP_SCREENSHOT"
DUO = "IPHONE_DUO_PROFILE"
# The sizes App Store Connect takes for each, for the problem a file of another size reports.
SIZES = {HEADER: "3840x1646", SEARCH: "1920x1280 to 3840x2560, or 5244x2950", DUO: "1398x2034 or 2007x2853"}


class Page(NamedTuple):
    """One platform's version page: its App Store Connect platform, the prefix of its listing fields and its
    screenshot sets as (folder of <lang>/*.png, display type)."""
    platform: str
    name: str
    prefix: str
    screenshots: tuple


def mac_page(listing: Listing):
    name = "macOS" if listing.platform == "MAC_OS" else listing.platform
    return Page(listing.platform, name, "apple", ((listing.screenshots, listing.screenshot_type),))


def ios_page(listing: Listing):
    return Page("IOS", "iOS", "ios", ((listing.iphone_screenshots, IPHONE_SCREENSHOTS),
                                      (listing.ipad_screenshots, IPAD_SCREENSHOTS)))


def info_fields(listing: Listing, lang, fields):
    return {"name": fields["apple.name"], "subtitle": fields["apple.subtitle"],
            "privacyPolicyUrl": listing.site(lang) + "privacy/"}


def version_fields(listing: Listing, lang, fields, prefix="apple"):
    site = listing.site(lang)
    want = {"description": fields[f"{prefix}.description"], "keywords": fields[f"{prefix}.keywords"],
            "promotionalText": fields[f"{prefix}.promo"], "supportUrl": site, "marketingUrl": site}
    if fields.get(f"{prefix}.whatsnew"):  # none on a first version: App Store Connect refuses it there
        want["whatsNew"] = fields[f"{prefix}.whatsnew"]
    return want


def review_image(listing: Listing, capture):
    """The window capture centered on a `review.size` canvas (sips, so the script stays standard-library only)."""
    out = listing.config.work_dir / "store" / "iap" / f"review-{capture.name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(capture.read_bytes())
    width, height = listing.review_size
    for args in (["-Z", str(height - 80)],
                 ["--padToHeightWidth", str(height), str(width), "--padColor", listing.review_background]):
        subprocess.run(["sips", *args, str(out)], check=True, capture_output=True)
    return out


def review_notes(listing: Listing):
    """The App Review notes: the text block under "App Review Information → Notes" in the review notes file."""
    text = listing.review_notes.read_text(encoding="utf-8")
    section = text.split("## App Review Information → Notes", 1)[1]
    return section.split("```text\n", 1)[1].split("\n```", 1)[0].strip()


def png_info(path):
    """(width, height, has alpha) from a PNG's IHDR chunk."""
    data = path.read_bytes()[:26]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} isn't a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"), data[25] in (4, 6)


def changed(want, have):
    return {k: v for k, v in want.items() if (have or {}).get(k) != v}


def md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


# --- API client -------------------------------------------------------------------------------------


def der_to_raw(der):
    """ECDSA signature: DER SEQUENCE { r, s } (openssl's output) -> raw r || s (what JWT wants)."""
    def read(i):
        tag, n = der[i], der[i + 1]
        i += 2
        if n & 0x80:
            size = n & 0x7F
            n = int.from_bytes(der[i:i + size], "big")
            i += size
        return tag, der[i:i + n], i + n

    tag, body, _ = read(0)
    assert tag == 0x30, "not a DER sequence"
    der = body
    _, r, i = read(0)
    _, s, _ = read(i)
    return b"".join(x.lstrip(b"\0").rjust(32, b"\0") for x in (r, s))


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(key_id, issuer, key_path, now=None):
    now = int(now or time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    claims = {"iss": issuer, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}
    signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
    der = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_path)],
                         input=signing_input.encode(), capture_output=True, check=True).stdout
    return f"{signing_input}.{b64url(der_to_raw(der))}"


class ApiError(Exception):
    pass


class Client:
    def __init__(self):
        key_id, issuer = os.environ.get("ASC_KEY_ID"), os.environ.get("ASC_ISSUER_ID")
        if not key_id or not issuer:
            sys.exit("Set ASC_KEY_ID and ASC_ISSUER_ID (see the header of this script).")
        path = os.environ.get("ASC_KEY_PATH") or next(
            (p for p in (Path.home() / d / f"AuthKey_{key_id}.p8"
                         for d in (".appstoreconnect/private_keys", "private_keys", ".private_keys"))
             if p.exists()), None)
        if not path or not Path(path).exists():
            sys.exit(f"No AuthKey_{key_id}.p8 in ~/.appstoreconnect/private_keys (or set ASC_KEY_PATH).")
        self.key = (key_id, issuer, path)
        self.token, self.minted = None, 0

    def request(self, method, path, body=None):
        if time.time() - self.minted > 600:
            self.token, self.minted = make_token(*self.key), time.time()
        url = path if path.startswith("http") else API + path
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                detail = e.read().decode(errors="replace")
                try:
                    detail = "; ".join(x.get("detail") or x.get("title", "") for x in json.loads(detail)["errors"])
                except (ValueError, KeyError, TypeError):
                    pass
                raise ApiError(f"{method} {url.removeprefix(API)}: {e.code} {detail}") from None

    def get_all(self, path):
        query = path.partition("?")[2]
        has_limit = any(x.startswith("limit=") for x in query.split("&"))  # App Store Connect refuses a second one
        items, url = [], path if has_limit else path + ("&" if query else "?") + "limit=200"
        while url:
            page = self.request("GET", url)
            items += page["data"]
            url = page.get("links", {}).get("next")
        return items

    def create(self, kind, attributes, relationships):
        rel = {name: {"data": {"type": t, "id": i}} for name, (t, i) in relationships.items()}
        body = {"data": {"type": kind, "attributes": attributes, "relationships": rel}}
        return self.request("POST", f"/v1/{kind}", body)["data"]

    def update(self, kind, id, attributes):
        self.request("PATCH", f"/v1/{kind}/{id}", {"data": {"type": kind, "id": id, "attributes": attributes}})

    def delete(self, kind, id):
        self.request("DELETE", f"/v1/{kind}/{id}")

    def upload(self, operations, data):
        for op in operations:
            chunk = data[op["offset"]:op["offset"] + op["length"]]
            headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
            req = urllib.request.Request(op["url"], data=chunk, method=op["method"], headers=headers)
            with urllib.request.urlopen(req):
                pass


# --- sync -------------------------------------------------------------------------------------------


class Sync:
    def __init__(self, listing: Listing, client, apply, log=print):
        self.listing, self.api, self.apply, self.log = listing, client, apply, log
        self.problems = []
        self.mac = mac_page(listing)
        self.ios = ios_page(listing) if listing.ios is not None else None

    def step(self, what, action):
        """Logs one change and runs it with --apply; an API error is recorded and the run goes on."""
        self.log("  " + what)
        if not self.apply:
            return None
        try:
            return action()
        except ApiError as e:
            self.problems.append(f"{what}: {e}")
            self.log(f"    ! {e}")
            return None

    def version(self, version_string, create, build, page=None):
        page = page or self.mac
        found = self.api.get_all(f"/v1/apps/{self.listing.app_id}/appStoreVersions?filter[platform]={page.platform}"
                                 f"&filter[versionString]={version_string}")
        if found:
            version = found[0]
            state = version["attributes"].get("appVersionState") or version["attributes"].get("appStoreState")
            self.log(f"{page.name} version {version_string}: {state}")
            if state not in EDITABLE:
                self.log("  Not editable in this state: only the promotional text can change.")
            # The release type follows `store.apple.releaseType` when it's set; without it, it's the owner's call.
            want = {"copyright": self.listing.copyright}
            if self.listing.release_type and state in EDITABLE:
                want["releaseType"] = self.listing.release_type
            attrs = changed(want, version["attributes"])
            if attrs:
                self.step(f"set {', '.join(attrs)} on the version",
                          lambda: self.api.update("appStoreVersions", version["id"], attrs))
        elif create:
            release = self.listing.release_type or "MANUAL"
            version = self.step(f"create {page.name} version {version_string} ({release.lower().replace('_', ' ')} release)",
                                lambda: self.api.create(
                "appStoreVersions", {"platform": page.platform, "versionString": version_string,
                                     "copyright": self.listing.copyright, "releaseType": release},
                {"app": ("apps", self.listing.app_id)}))
            if version is None and self.apply:
                return None
            version = version or {"id": None, "attributes": {}}  # dry run: it doesn't exist yet
        else:
            sys.exit(f"No {page.name} version {version_string} in App Store Connect; pass --create to add it.")
        if build:
            self.attach_build(version, build, page.platform)
        return version

    def first_version(self, version, page):
        """Whether the version is the platform's first: no other version of it in App Store Connect."""
        others = self.api.get_all(f"/v1/apps/{self.listing.app_id}/appStoreVersions?filter[platform]={page.platform}")
        return not [v for v in others if v["id"] != version["id"]]

    def attach_build(self, version, number, platform):
        builds = self.api.get_all(f"/v1/builds?filter[app]={self.listing.app_id}&filter[version]={number}"
                                  f"&filter[preReleaseVersion.platform]={platform}")
        if not builds:
            self.problems.append(f"build {number} isn't in App Store Connect yet (still uploading?)")
            return
        build = builds[0]
        if build["attributes"].get("processingState") != "VALID":
            self.problems.append(f"build {number} is {build['attributes'].get('processingState')}, not VALID yet")
            return
        if version["id"]:
            current = self.api.request("GET", f"/v1/appStoreVersions/{version['id']}/build").get("data")
            if current and current["id"] == build["id"]:
                self.log(f"  build {number} is attached")
                return
        self.step(f"attach build {number}", lambda: self.api.request(
            "PATCH", f"/v1/appStoreVersions/{version['id']}/relationships/build",
            {"data": {"type": "builds", "id": build["id"]}}))

    def app_info(self):
        infos = self.api.get_all(f"/v1/apps/{self.listing.app_id}/appInfos")
        live = {"READY_FOR_DISTRIBUTION", "READY_FOR_SALE", "REPLACED_WITH_NEW_INFO"}
        return next((i for i in infos if (i["attributes"].get("state") or i["attributes"].get("appStoreState"))
                     not in live), infos[0])

    def localizations(self, kind, parent_path):
        return {x["attributes"]["locale"]: x for x in self.api.get_all(parent_path)} if parent_path else {}

    def sync_locale(self, kind, existing, locale, want, parent):
        """Creates or patches one localization; returns it (None in a dry run of a new one)."""
        have = existing.get(locale)
        if have is None:
            return self.step(f"{locale}: add the language ({kind})",
                             lambda: self.api.create(kind, {"locale": locale, **want}, parent))
        diff = changed(want, have["attributes"])
        if diff:
            self.step(f"{locale}: update {', '.join(diff)}", lambda: self.api.update(kind, have["id"], diff))
        return have

    def text(self, langs, version, extra, write, page=None):
        """Syncs the text when write is set (App Information, shared by the platforms, then the page's version);
        returns {locale: (lang, version localization or None)}."""
        page = page or self.mac
        first = write and self.first_version(version, page)
        vers = self.localizations("appStoreVersionLocalizations", version["id"] and
                                  f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations")
        if write:
            info = self.app_info()
            infos = self.localizations("appInfoLocalizations", f"/v1/appInfos/{info['id']}/appInfoLocalizations")
            # Adding a language to App Information also adds it to the version, empty: add those first, then
            # read the version's languages again so they are updated instead of created twice.
            for lang in langs:
                fields = self.listing.fields(lang)
                for locale in [self.listing.locale[lang]] + [x for x in self.listing.extra_locales.get(lang, []) if extra or x in vers]:
                    self.sync_locale("appInfoLocalizations", infos, locale, info_fields(self.listing, lang, fields),
                                     {"appInfo": ("appInfos", info["id"])})
            if self.apply:
                vers = self.localizations("appStoreVersionLocalizations", version["id"] and
                                          f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations")
        result = {}
        for lang in langs:
            fields = self.listing.fields(lang)
            want = version_fields(self.listing, lang, fields, page.prefix) if write else None
            if first:
                want.pop("whatsNew", None)
            for locale in [self.listing.locale[lang]] + [x for x in self.listing.extra_locales.get(lang, []) if extra or x in vers]:
                if not write:
                    result[locale] = (lang, vers.get(locale))
                    continue
                result[locale] = (lang, self.sync_locale(
                    "appStoreVersionLocalizations", vers, locale, want,
                    {"appStoreVersion": ("appStoreVersions", version["id"])}))
        return result

    def create_iap(self, product_id, spec):
        """Creates an in-app purchase with its price and availability; returns it (None in a dry run)."""
        self.log(f"In-app purchase {product_id}: missing")
        product = self.step(f"  create it: {spec['type'].lower()}, Family Sharing {'on' if spec['familySharing'] else 'off'}",
                            lambda: self.api.request("POST", "/v2/inAppPurchases", {"data": {
                                "type": "inAppPurchases",
                                "attributes": {"name": spec["reference"], "productId": product_id,
                                               "inAppPurchaseType": spec["type"], "familySharable": spec["familySharing"],
                                               **({"reviewNote": spec["reviewNote"]} if spec.get("reviewNote") else {})},
                                "relationships": {"app": {"data": {"type": "apps", "id": self.listing.app_id}}}}})["data"])
        if product is None:
            self.log(f"  price {spec['price']} in {self.listing.base_territory}, available in every territory")
            return None

        def price():
            points = self.api.get_all(f"/v2/inAppPurchases/{product['id']}/pricePoints"
                                      f"?filter[territory]={self.listing.base_territory}")
            point = next((x for x in points if x["attributes"]["customerPrice"] == spec["price"]), None)
            if point is None:
                raise ApiError(f"no {spec['price']} price point in {self.listing.base_territory}")
            self.api.request("POST", "/v1/inAppPurchasePriceSchedules", {
                "data": {"type": "inAppPurchasePriceSchedules", "relationships": {
                    "inAppPurchase": {"data": {"type": "inAppPurchases", "id": product["id"]}},
                    "baseTerritory": {"data": {"type": "territories", "id": self.listing.base_territory}},
                    "manualPrices": {"data": [{"type": "inAppPurchasePrices", "id": "${base}"}]}}},
                "included": [{"type": "inAppPurchasePrices", "id": "${base}", "attributes": {"startDate": None},
                              "relationships": {
                                  "inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": product["id"]}},
                                  "inAppPurchasePricePoint": {"data": {"type": "inAppPurchasePricePoints",
                                                                       "id": point["id"]}}}}]})
        self.step(f"  price {spec['price']} in {self.listing.base_territory}", price)

        def availability():
            territories = [{"type": "territories", "id": t["id"]} for t in self.api.get_all("/v1/territories")]
            self.api.request("POST", "/v1/inAppPurchaseAvailabilities", {"data": {
                "type": "inAppPurchaseAvailabilities", "attributes": {"availableInNewTerritories": True},
                "relationships": {"inAppPurchase": {"data": {"type": "inAppPurchases", "id": product["id"]}},
                                  "availableTerritories": {"data": territories}}}})
        self.step("  available in every territory", availability)
        return product

    def iaps(self, langs, extra):
        products = {p["attributes"]["productId"]: p for p in self.api.get_all(f"/v1/apps/{self.listing.app_id}/inAppPurchasesV2")}
        for product_id, spec in self.listing.iaps.items():
            product = products.get(product_id) or self.create_iap(product_id, spec)
            if product is None:
                continue
            self.log(f"In-app purchase {product_id}")
            if spec.get("reviewNote") and product["attributes"].get("reviewNote") != spec["reviewNote"]:
                self.step("  set the review note", lambda product=product: self.api.request(
                    "PATCH", f"/v2/inAppPurchases/{product['id']}", {"data": {
                        "type": "inAppPurchases", "id": product["id"], "attributes": {"reviewNote": spec["reviewNote"]}}}))
            existing = self.localizations("inAppPurchaseLocalizations",
                                          f"/v2/inAppPurchases/{product['id']}/inAppPurchaseLocalizations")
            if spec.get("reviewShot"):
                self.iap_review_screenshot(product, spec["screenshot"])
            for lang in langs:
                fields = self.listing.fields(lang)
                want = {"name": fields[spec["name"]], "description": fields[spec["description"]]}
                for locale in [self.listing.locale[lang]] + [x for x in self.listing.extra_locales.get(lang, []) if extra or x in existing]:
                    self.sync_locale("inAppPurchaseLocalizations", existing, locale, want,
                                     {"inAppPurchaseV2": ("inAppPurchases", product["id"])})

    def iap_review_screenshot(self, product, capture):
        """Uploads the App Review screenshot for one in-app purchase unless the same file is already there and
        App Store Connect accepted it."""
        if capture is None or not capture.exists():
            self.problems.append(f"no review screenshot {capture} for {product['attributes']['productId']} "
                                 "(the app's screenshots.sh)")
            return
        f = review_image(self.listing, capture)
        have = self.api.request("GET", f"/v2/inAppPurchases/{product['id']}/appStoreReviewScreenshot").get("data")
        failed = have and (have["attributes"].get("assetDeliveryState") or {}).get("state") == "FAILED"
        if have and not failed and have["attributes"].get("sourceFileChecksum") == md5(f):
            return
        kind = "inAppPurchaseAppStoreReviewScreenshots"

        def upload():
            if have:
                self.api.delete(kind, have["id"])
            data = f.read_bytes()
            item = self.api.create(kind, {"fileName": f.name, "fileSize": len(data)},
                                   {"inAppPurchaseV2": ("inAppPurchases", product["id"])})
            self.api.upload(item["attributes"]["uploadOperations"], data)
            self.api.update(kind, item["id"], {"uploaded": True, "sourceFileChecksum": md5(f)})
        self.step(f"  review screenshot: {'replace' if have else 'upload'} {f.name}", upload)

    def media(self, localization, locale, files, set_kind, set_type_attr, set_type, item_kind):
        """Replaces a screenshot or preview set when its files differ from ours (compared by MD5)."""
        sets_path = f"/v1/appStoreVersionLocalizations/{localization['id']}/{set_kind}" if localization else None
        sets = self.api.get_all(sets_path) if sets_path else []
        media_set = next((s for s in sets if s["attributes"][set_type_attr] == set_type), None)
        have = self.api.get_all(f"/v1/{set_kind}/{media_set['id']}/{item_kind}") if media_set else []
        if [x["attributes"].get("sourceFileChecksum") for x in have] == [md5(f) for f in files]:
            return
        label = f"screenshots ({set_type})" if item_kind == "appScreenshots" else "preview"
        self.log(f"  {locale}: {label}: replace {len(have)} with {len(files)} ({', '.join(f.name for f in files)})")
        if not self.apply or not localization:
            return
        if media_set is None:
            media_set = self.step(f"{locale}: create the {label} set", lambda: self.api.create(
                set_kind, {set_type_attr: set_type},
                {"appStoreVersionLocalization": ("appStoreVersionLocalizations", localization["id"])}))
            if media_set is None:
                return
        for item in have:
            self.step(f"{locale}: remove {item['attributes'].get('fileName')}",
                      lambda item=item: self.api.delete(item_kind, item["id"]))
        rel = {set_kind[:-1]: (set_kind, media_set["id"])}
        for f in files:
            def upload(f=f):
                data = f.read_bytes()
                attrs = {"fileName": f.name, "fileSize": len(data)}
                if item_kind == "appPreviews":
                    attrs["mimeType"] = "video/mp4"
                item = self.api.create(item_kind, attrs, rel)
                self.api.upload(item["attributes"]["uploadOperations"], data)
                self.api.update(item_kind, item["id"], {"uploaded": True, "sourceFileChecksum": md5(f)})
            self.step(f"{locale}: upload {f.name}", upload)

    def review(self, version, page=None):
        print(("" if page is None or page == self.mac else f"{page.name} ") + "App Review")
        if not self.listing.review_notes.exists():
            self.log(f"  no {self.listing.review_notes.name} in {self.listing.dir.name}/: the notes are left alone")
            return
        notes = review_notes(self.listing)
        if not version["id"]:
            self.log("  set the notes (once the version exists)")
            return
        detail = self.api.request("GET", f"/v1/appStoreVersions/{version['id']}/appStoreReviewDetail").get("data")
        if detail is None:
            self.step(f"  create the review details with the notes ({len(notes)} characters)", lambda: self.api.create(
                "appStoreReviewDetails", {"notes": notes}, {"appStoreVersion": ("appStoreVersions", version["id"])}))
        elif (detail["attributes"].get("notes") or "").strip() != notes:
            self.step(f"  update the notes ({len(notes)} characters)",
                      lambda: self.api.update("appStoreReviewDetails", detail["id"], {"notes": notes}))

    def screenshots(self, localizations, page=None):
        for locale, (lang, localization) in localizations.items():
            for root, display_type in (page or self.mac).screenshots:
                folder = root / lang
                files = sorted(folder.glob("*.png"))
                if not files:
                    self.problems.append(f"{lang}: no screenshots in {folder} (store-screenshots.py, or the app's own script)")
                    continue
                self.media(localization, locale, files, "appScreenshotSets", "screenshotDisplayType", display_type,
                           "appScreenshots")

    def previews(self, localizations):
        for locale, (lang, localization) in localizations.items():
            f = self.listing.previews / f"preview-{lang}.mp4"
            if not f.exists():
                self.problems.append(f"{lang}: no {f}")
                continue
            self.media(localization, locale, [f], "appPreviewSets", "previewType", self.listing.preview_type,
                       "appPreviews")

    def placement_groups(self, placement_type, width, height):
        """The placement groups of `placement_type` whose specification takes a width x height image, from the App
        Asset Library's reference data (DEFAULT_PROFILE, platform ANY, for the header and the search results asset in
        October 2026): its identifiers aren't an enumeration, so they're read, never hard-coded. A Mac group, should
        one appear, is left out: these are the iOS page's."""
        refs = self.__dict__.setdefault("_refs", {})
        if placement_type not in refs:
            refs[placement_type] = self.api.request(
                "GET", f"/v1/appAssetLibraryRefData?filter[placementTypes]={placement_type}")["data"][0]["attributes"]
        ref = refs[placement_type]
        specs = {s["specId"] for s in ref.get("imageSpecs", []) if placement_type in s.get("compatiblePlacementTypes", [])
                 and s["dimensions"]["minWidth"] <= width <= s["dimensions"]["maxWidth"]
                 and s["dimensions"]["minHeight"] <= height <= s["dimensions"]["maxHeight"]}
        allowed = {g for f in ref.get("features", []) if f.get("featureId") == "APP_STORE_VERSIONS"
                   for p in f.get("placementPolicies", []) if p.get("placementType") == placement_type
                   for limit in p.get("groupLimits", []) for g in limit.get("groupIds", [])}
        platforms = {g["placementProfileGroupId"]: g.get("platform", "") for g in ref.get("placementProfileGroups", [])}
        return sorted(m["placementGroupId"] for t in ref.get("placementTypes", []) if t.get("placementTypeId") == placement_type
                      for m in t.get("specMappings", []) if specs & set(m.get("specs", []))
                      and m["placementGroupId"] in allowed
                      and not platforms.get(m["placementGroupId"], "MAC").startswith("MAC"))

    def header(self, localizations):
        """Places the product page header on every language's iOS version page, in each placement group that takes its
        size, replacing a header with another file there (the uploaded file's name carries its MD5)."""
        f = self.listing.ios_header
        if not f.exists():
            self.problems.append(f"no header {f} (3840x1646, no alpha)")
            return
        self.place(localizations, HEADER, lambda lang: [f], "header", "Product page header")

    def search_results(self, localizations):
        """Places the search results asset on every language's iOS version page, like the header."""
        f = self.listing.ios_search
        if not f.exists():
            self.problems.append(f"no search results asset {f} ({SIZES[SEARCH]}, no alpha)")
            return
        self.place(localizations, SEARCH, lambda lang: [f], "search", "Search results")

    def duo_screenshots(self, localizations):
        """Places each language's iPhone Duo screenshots, in file order, in the Duo's placement group."""
        def files(lang):
            found = sorted((self.listing.duo_screenshots / lang).glob("*.png"))
            if not found:
                self.problems.append(f"{lang}: no screenshots in {self.listing.duo_screenshots / lang}")
            return found
        self.place(localizations, SCREENSHOT, files, "duo", "iPhone Duo screenshot", group=DUO)

    def place(self, localizations, placement_type, files_for, prefix, reference, group=None):
        """Syncs one placement type on each language's iOS version page: `files_for(lang)` gives the PNGs, placed in
        that order in every placement group that takes their size (or in `group` alone, which must take it). A group
        whose images all match (the uploaded file's name carries its MD5) is left alone; otherwise its placements are
        replaced. Each file is uploaded to the app's Asset Library once, however many languages and runs use it: an
        image already there under that file name is reused, and each upload's reference name (unique in a library) is
        the label plus the file name."""
        images = None  # uploaded file name -> image id ("" in a dry run), read from the library on first use
        groups_for = {}  # (width, height) -> groups
        label = reference[0].lower() + reference[1:]

        def library():
            return self.api.request("GET", f"/v1/apps/{self.listing.app_id}/assetLibrary")["data"]["id"]

        def upload(f, name):
            data = f.read_bytes()
            item = self.api.create("appAssetLibraryImages", {"fileName": name, "fileSize": len(data),
                                                             "category": "CREATIVE_ASSETS",
                                                             "referenceName": f"{reference} {name}"},
                                   {"assetLibrary": ("appAssetLibraries", library())})
            self.api.upload(item["attributes"]["uploadOperations"], data)
            self.api.update("appAssetLibraryImages", item["id"], {"uploaded": True})
            return item

        for locale, (lang, localization) in localizations.items():
            files = files_for(lang)
            if not files:
                continue
            groups = None
            for f in files:
                width, height, alpha = png_info(f)
                if alpha:
                    self.problems.append(f"{f.name} has an alpha channel; App Store Connect refuses it for a {label}")
                    groups = []
                    break
                if (width, height) not in groups_for:
                    groups_for[width, height] = self.placement_groups(placement_type, width, height)
                fits = [g for g in groups_for[width, height] if group in (None, g)]
                if not fits:
                    self.problems.append(f"{f.name}: no {label} placement takes {width}x{height} "
                                         f"({SIZES.get(group or placement_type, 'see App Store Connect')})")
                    groups = []
                    break
                groups = fits if groups is None else [g for g in groups if g in fits]
            if not groups:
                continue
            names = [f"{prefix}-{md5(f)[:12]}{f.suffix}" for f in files]
            placed = {}  # group -> [(placement id, file name)]
            if localization:
                page = self.api.request("GET", f"/v1/appStoreVersionLocalizations/{localization['id']}/placements"
                                               f"?filter[placementType]={placement_type}&include=image&limit=50") or {}
                file_names = {i["id"]: i["attributes"].get("fileName") for i in page.get("included", [])}
                for p in page.get("data") or []:
                    if p["attributes"].get("placementType", placement_type) != placement_type:
                        continue
                    image_id = ((p.get("relationships", {}).get("image") or {}).get("data") or {}).get("id")
                    placed.setdefault(p["attributes"]["placementGroup"], []).append((p["id"], file_names.get(image_id)))
            for g in groups:
                have = placed.get(g, [])
                if sorted(n for _, n in have) == sorted(names):
                    continue
                if images is None:
                    images = {i["attributes"].get("fileName"): i["id"]
                              for i in self.api.get_all(f"/v1/appAssetLibraries/{library()}/images")}
                for f, name in zip(files, names):
                    if name not in images:
                        item = self.step(f"upload {f.name} ({prefix}) to the asset library",
                                         lambda f=f, name=name: upload(f, name))
                        if self.apply and item is None:
                            return
                        images[name] = (item or {}).get("id", "")
                what = ", ".join(f.name for f in files)
                self.log(f"  {locale}: {label} ({g}): " + (f"replace {len(have)} with {what}" if have else f"place {what}"))
                if not self.apply or not localization:
                    continue
                for placement_id, old in have:
                    self.step(f"{locale}: remove the {label} {old}",
                              lambda placement_id=placement_id: self.api.delete("appAssetLibraryPlacements", placement_id))
                for name in names:
                    self.step(f"{locale}: place {name}", lambda g=g, name=name: self.api.create(
                        "appAssetLibraryPlacements", {"placementType": placement_type, "placementGroup": g},
                        {"appStoreVersionLocalization": ("appStoreVersionLocalizations", localization["id"]),
                         "image": ("appAssetLibraryImages", images[name])}))


def status(listing: Listing, api, version_string):
    """Prints what App Store Connect has for the version (the Mac's, and the iOS one with `store.apple.ios`) and what a
    submission still needs. The platforms number their versions apart, so one may lack it; exits non-zero only when
    none has it. Reads only."""
    missing = []
    mac = status_version(listing, api, version_string, mac_page(listing), missing)
    if not mac:
        print(f"No macOS version {version_string} in App Store Connect.")
    ios = listing.ios is not None and status_version(listing, api, version_string, ios_page(listing), missing)
    if listing.ios is not None and not ios:
        print(f"No iOS version {version_string} yet (push.py --create adds it)")
        missing.append(f"the iOS version {version_string}")
    if not mac and not ios:
        sys.exit(f"No version {version_string} in App Store Connect.")
    products = {p["attributes"]["productId"]: p for p in api.get_all(f"/v1/apps/{listing.app_id}/inAppPurchasesV2")}
    for product_id in listing.iaps:
        product = products.get(product_id)
        if not product:
            print(f"  {product_id}: not found")
            missing.append(f"in-app purchase {product_id}")
            continue
        shot = api.request("GET", f"/v2/inAppPurchases/{product['id']}/appStoreReviewScreenshot").get("data")
        delivery = ((shot or {}).get("attributes", {}).get("assetDeliveryState") or {})
        shot_state = delivery.get("state", "none") if shot else "none"
        errors = ", ".join(e.get("code", "") for e in delivery.get("errors") or [])
        print(f"  {product_id}: {product['attributes'].get('state')}, review screenshot {shot_state}"
              + (f" ({errors})" if errors else ""))
        if shot_state in ("none", "FAILED"):
            missing.append(f"{product_id} review screenshot")
    print("\nStill needed: " + ("; ".join(missing) if missing else "nothing found missing"))


def status_version(listing: Listing, api, version_string, page, missing):
    """Prints one platform's version: its state, build, App Review details and, per language, its screenshots per
    display type and empty fields; adds what's missing to `missing`. False when App Store Connect has no such version."""
    found = api.get_all(f"/v1/apps/{listing.app_id}/appStoreVersions?filter[platform]={page.platform}"
                        f"&filter[versionString]={version_string}")
    if not found:
        return False
    version = found[0]
    attrs = version["attributes"]
    tag = "" if page == mac_page(listing) else f"{page.name} "  # the Mac's lines read as before
    print(f"{tag}Version {version_string}: {attrs.get('appVersionState') or attrs.get('appStoreState')}, "
          f"release {attrs.get('releaseType')}, copyright {attrs.get('copyright')!r}")
    build = api.request("GET", f"/v1/appStoreVersions/{version['id']}/build").get("data")
    print(f"  build: {build['attributes']['version'] if build else 'none attached'}")
    if not build:
        missing.append(f"a build on the {tag}version page")
    builds = api.get_all(f"/v1/builds?filter[app]={listing.app_id}&filter[preReleaseVersion.platform]={page.platform}"
                         "&sort=-uploadedDate")
    print("  builds: " + ", ".join(f"{b['attributes']['version']} {b['attributes'].get('processingState')}"
                                   for b in builds[:6]))
    review = api.request("GET", f"/v1/appStoreVersions/{version['id']}/appStoreReviewDetail").get("data")
    if review:
        r = review["attributes"]
        contact = all(r.get(k) for k in ("contactFirstName", "contactLastName", "contactPhone", "contactEmail"))
        print(f"  App Review: contact {'complete' if contact else 'incomplete'}, notes "
              f"{len(r.get('notes') or '')} characters, sign-in {'required' if r.get('demoAccountRequired') else 'not required'}")
        if not contact:
            missing.append(f"the {tag}App Review contact (name, phone, email)")
    else:
        print("  App Review: no details yet")
        missing.append(f"the {tag}App Review contact (name, phone, email)")
    for loc in api.get_all(f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations"):
        shots = {}
        for s in api.get_all(f"/v1/appStoreVersionLocalizations/{loc['id']}/appScreenshotSets"):
            kind = s["attributes"].get("screenshotDisplayType")
            shots[kind] = shots.get(kind, 0) + len(api.get_all(f"/v1/appScreenshotSets/{s['id']}/appScreenshots"))
        a = loc["attributes"]
        empty = [k for k in ("description", "keywords", "supportUrl") if not a.get(k)]
        counts = [(SCREENSHOT_NAMES.get(t, ""), shots.get(t, 0)) for _, t in page.screenshots]
        header = ""
        if page.platform == "IOS":
            def placements(placement_type, group=None):
                found = (api.request("GET", f"/v1/appStoreVersionLocalizations/{loc['id']}/placements"
                                            f"?filter[placementType]={placement_type}") or {}).get("data") or []
                return [p for p in found if p["attributes"].get("placementType", placement_type) == placement_type
                        and group in (None, p["attributes"].get("placementGroup"))]
            duo, placed, search = len(placements(SCREENSHOT, DUO)), len(placements(HEADER)), len(placements(SEARCH))
            if listing.duo_screenshots.exists():
                counts.append(("iPhone Duo", duo))
            header = (f", {placed} header placement{'' if placed == 1 else 's'}"
                      f", {search} search results placement{'' if search == 1 else 's'}")
        print(f"  {tag}{a['locale']}: " + ", ".join(f"{n} {name + ' ' if name else ''}screenshots" for name, n in counts)
              + header + (f", empty: {', '.join(empty)}" if empty else ""))
        missing += [f"{tag}{a['locale']} {name + ' ' if name else ''}screenshots" for name, n in counts if not n]
        missing += [f"{tag}{a['locale']} {k}" for k in empty]
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--version", required=True, help="the version string, for example 1.0.2")
    parser.add_argument("--create", action="store_true", help="create the version if it doesn't exist")
    parser.add_argument("--build", help="attach this build number (CFBundleVersion) to the version")
    listing = load()
    default = "text,iap,screenshots,review" + (",ios-text,ios-screenshots" if listing.ios is not None else "") + (
        ",ios-header" if listing.ios is not None and listing.ios_header.exists() else "") + (
        ",ios-search" if listing.ios is not None and listing.ios_search.exists() else "")
    parser.add_argument("--only", default=default,
                        help=f"comma-separated parts: {', '.join(PARTS)} (default: {default}; the ios- parts need "
                             "store.apple.ios)")
    parser.add_argument("--extra-locales", action="store_true", help="also add en-GB, es-MX, fr-CA, ...")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--status", action="store_true", help="read only: what App Store Connect has and still needs")
    parser.add_argument("langs", nargs="*", default=listing.langs)
    args = parser.parse_args()
    parts = set(args.only.split(","))
    if parts - set(PARTS) or not set(args.langs) <= set(listing.langs):
        parser.error(f"--only takes {', '.join(PARTS)}; languages are {' '.join(listing.langs)}")
    if listing.ios is None and any(p.startswith("ios-") for p in parts):
        parser.error("the ios- parts need store.apple.ios in companion.json")
    if not listing.app_id:
        sys.exit("companion.json has no store.apple.appId")
    if args.status:
        return status(listing, Client(), args.version)
    import check

    if not check.run(listing, args.langs):
        sys.exit("Fix the listing first (laching/tools/store/check.py).")

    sync = Sync(listing, Client(), args.apply)
    if not args.apply:
        print("Dry run: nothing is written. Add --apply to push.\n")
    # Each platform's version only when one of its parts asks for it: the Mac and iOS apps have their own version
    # numbers, so a push for one must not create the other's version of that number. `review` goes with the platform
    # whose parts are asked for, or with both when it's alone.
    mac_parts, ios_parts = parts & {"text", "screenshots", "previews"}, parts & {"ios-text", "ios-screenshots", "ios-header", "ios-search"}
    mac = bool(mac_parts) or ("review" in parts and not ios_parts)
    ios = sync.ios is not None and (bool(ios_parts) or ("review" in parts and not mac_parts))
    if mac:
        version = sync.version(args.version, args.create, args.build)
        if version is None:
            sys.exit("\n".join(sync.problems))
    localizations = {}
    if parts & {"text", "screenshots", "previews"}:
        print("Text")
        localizations = sync.text(args.langs, version, args.extra_locales, write="text" in parts)
    if "iap" in parts:
        sync.iaps(args.langs, args.extra_locales)
    if "review" in parts and mac:
        sync.review(version)
    if "screenshots" in parts:
        print("Screenshots")
        sync.screenshots(localizations)
    if "previews" in parts:
        print("Previews")
        sync.previews(localizations)
    if ios:
        print("iOS")
        ios_version = sync.version(args.version, args.create, args.build, sync.ios)
        if ios_version is None:
            sys.exit("\n".join(sync.problems))
        if "review" in parts:
            sync.review(ios_version, sync.ios)
    if ios_parts:
        print("iOS text")
        ios_localizations = sync.text(args.langs, ios_version, args.extra_locales, write="ios-text" in parts,
                                      page=sync.ios)
        if "ios-screenshots" in parts:
            print("iOS screenshots")
            sync.screenshots(ios_localizations, sync.ios)
            if listing.duo_screenshots.exists():
                sync.duo_screenshots(ios_localizations)
        if "ios-header" in parts:
            print("iOS header")
            sync.header(ios_localizations)
        if "ios-search" in parts:
            print("iOS search results")
            sync.search_results(ios_localizations)
    if sync.problems:
        print("\nProblems:\n" + "\n".join(f"  {p}" for p in sync.problems))
        sys.exit(1)
    print("\nDone." if args.apply else "\nDry run done.")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 on a cp1252 Windows console too (companion.utf8_console)
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
