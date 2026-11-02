---
name: landing-copy
description: Update what a Laching companion's landing page says when a platform, a store or a purchase changes (privacy and terms in every language, the store buttons, the platform line, the JSON-LD, the store listings), then run laching-landing check. Use when the app reaches a new platform or store, a listing goes live, a purchase is added, removed or repriced, or a release changes what the landing claims.
---

# Landing copy

The landing page is `landing.dir` in `companion.json`, its own repository built with `@laching/landing`. Its legal
pages are hand-written HTML, one per language, and nothing rebuilds them when the app changes: a new store or platform
that the privacy policy never names is a claim the page doesn't make. The app's own places (the i18n keys of its
platform line, its JSON-LD, its listing folder) are in `.agents/notes/landing-copy.md` when it exists: read it first.

## What changes

Go through each; most changes touch all of them.

1. **Privacy and terms, in every language.** Name each store the app sells through (App Store, Microsoft Store, Google
   Play: never translated) and each platform it runs on, in the app's own words. Say what a purchase buys, which store
   takes the payment and that refunds go through that store. Write the source language's pages, then the others: they
   aren't catalog keys, so `l10n.py` doesn't carry them, but the `localization` skill's rules do (transcreate, the
   app's glossary, one sub-agent per language with only its two files, the owner's review languages reported).
2. **The store buttons.** `appStoreUrl` (`null` shows "coming soon" until the listing is live) and any other store's
   button in the template, with its label keys in every locale.
3. **The platform line.** The locale keys that list where the app runs, or say a platform is coming, in every
   language: through the `localization` skill.
4. **The JSON-LD.** `operatingSystem`, `installUrl`, `sameAs` and `offers` in the `jsonLd` option match the stores
   and prices that are live, and leave out the ones that aren't.
5. **`stores` and `platforms`** in `companionLanding`, so the check holds the legal pages to them: each store's listing
   URL, or `null` while it isn't live; each platform as the pages write it. `nameAliases` is for a language whose
   pages really write a name differently, never to quiet the check.
6. **The store listings.** Each store's description and What's New in every language (`store` in `companion.json`;
   `tools/store/check.py`, then the store's push tool), with the same platforms and purchases. App Store Connect gets
   `<site>/<lang>/privacy/` for each store language and the home page as the support URL: each store language needs
   its privacy page, and each home page its contact link.
7. **The README** of the app and of the landing, where they list platforms, stores or prices.

## Check

In `landing.dir`, after building it:

```sh
npx vp build && npx laching-landing check
```

It prints one line per problem: a dead link or anchor, one-way hreflang, a page missing in a language, a home page
without its contact link, a legal page that never names a configured store or platform. Fix each in the page, in every
language it names, and run it again until it passes. Then build the app's landing check if it has one, commit the
landing in its own repository, and the app's submodule pointer with the app's change.
