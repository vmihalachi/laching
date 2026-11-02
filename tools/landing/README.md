# @laching/landing

A desk companion's landing page as Vite plugins: every home page rendered from one template per language, the
hand-translated legal pages given canonical and hreflang links, `sitemap.xml`, the Cloudflare `_headers` with a CSP
that hashes the inline scripts, and the social cards and icons rendered from SVG with resvg. The page itself (its
template, script, styles, data and brand) stays the app's; this is the plumbing every Laching app's page repeated.

```ts
import { companionLanding, escapeHtml, scriptJson } from "@laching/landing";
import { defineConfig } from "vite-plus";

const landing = companionLanding({
  root: __dirname,
  siteUrl: "https://example.app",
  appStoreUrl: null, // "coming soon" until the listing is live
  contactEmail: "hello@example.app",
  languages, // { code, path, label, nativeName, ogLocale, aliases?, notFor? }[]
  defaultLanguageCode: "en",
  locales, // code → src/i18n/<code>.json
  placeholders: (key, language, locale) =>
    key === "runtimeJson" ? scriptJson(locale.runtime) : undefined,
  jsonLd: (language, locale) => ({
    "@context": "https://schema.org",
    "@type": "SoftwareApplication" /* … */,
  }),
  og: {
    dir: `${__dirname}/og`,
    iconPath: `${__dirname}/public/favicon.svg`,
    fontFamily: "Fredoka",
    iconBackground: "#FFF8E7",
  },
});

export default defineConfig({ ...landing, fmt: {}, lint: {} });
```

The template's `{{key}}` placeholders are the app's own (`placeholders`, asked first), then the generated ones:
`langRedirectScript`, `storeButton`, `storeButtonBig`, `contactEmail`, `legal<Page>Url`, `jsonLd`,
`languageSwitcher` (`"menu"`, `"inline"` or a function), `hreflangLinks`, `ogLocale`, `ogLocaleAlternates`,
`moreFrom`, then the locale's dotted keys. Locale files must share the default's keys. `moreFrom` lists the sibling
apps for a footer: `{ apps: { name, url, tagline?: (locale, language) => string }[], label: (locale, language) =>
string, builtOn?: { url, label: (locale, language) => string } }`, rendered as a `.more-from` block
(`.more-from-label`, `.more-from-apps`, `.more-from-tagline`, `.built-on`) with every value escaped and no styles; an
app whose `url` is `siteUrl` is left out, so the landings can share one list, and without the option `{{moreFrom}}`
renders nothing. Like every placeholder it is filled only in the template's pages (the home pages and `404.html`):
hand-written pages get their canonical and hreflang tags but no placeholders, so their footers write the list
themselves. `pages` adds hand-written pages beyond the legal ones (a comparison, a guide), each in the languages it
lists, with canonical, hreflang and sitemap entries among those languages only. `csp.mediaSrc` allows recorded audio,
`headers` adds `_headers` rules, `extraPlugins` runs the app's own plugins between the hosting and social-asset ones.

## Checking the built site

`laching-landing check [dist] [--config vite.config.ts]` reads the landing's Vite config (the one in the current folder
unless `--config` names another) and checks the built site in `dist` (the config's `build.outDir` unless given),
offline, after `vp build`. It prints one line per problem and exits 1 on any, 2 on an unknown argument, a config that is
missing or doesn't load, no `companionLanding` in it or no built site. `checkLanding(dist, options)` is the same check
as a function, returning the problems. It finds:

- a dead link (`href`, `src`, `srcset`, resolved as Cloudflare Pages serves `dist`) or a dead `#anchor`;
- hreflang that isn't reciprocal, a missing or extra `x-default`, a page missing from its own set, and a canonical that
  isn't the page itself;
- a language without its home page, a legal page or one of `pages`, and, without a `support` legal page, a home page
  with no `mailto:` link to `contactEmail` (the home page is then the support URL);
- a legal page, in any language, that never names one of `stores` (`{ apple?, microsoft?, google? }`, each the
  listing's URL or `null` before it's live; named App Store, Microsoft Store and Google Play) or `platforms` (the app's
  own words, such as `["Mac", "Windows"]`). `nameAliases` accepts other words per language and name, such as
  `{ it: { Mac: ["macOS"] } }`.

`stores`, `platforms` and `nameAliases` change no page: they're what the check holds the legal pages to. The check is
a separate step, not part of the build, so a landing adopts it in its deploy when its pages pass.

Released from Laching's `release.yml` on each `vX.Y.Z` tag that changes `tools/landing` since the version on npm, at
the tag's version (the `version` here is a placeholder), so its versions skip the Laching releases that leave it alone:
its `npm` job publishes the tarball to npm through trusted publishing (no stored token; provenance attached). The
release's dispatch is the human gate: nothing is published without someone running `release.yml`. The trusted
publisher on npmjs.com is repository `vmihalachi/laching`, workflow `release.yml`, environment `npm`, allowed to
`npm publish`; the package's publishing access is "Require two-factor authentication and disallow tokens". npm attaches
a trusted publisher only to a package that exists, so the package is created once by hand, then `npm trust github
@laching/landing --repo vmihalachi/laching --file release.yml --env npm` adds the publisher.

Peer dependencies: `vite-plus` and `@resvg/resvg-js`. Develop with `npm ci`, `npx vp test run`,
`npx vp check` and `npx tsc --noEmit`.
