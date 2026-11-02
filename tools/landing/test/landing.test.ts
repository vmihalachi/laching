import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { build } from "vite-plus";
import { afterAll, beforeAll, describe, expect, it } from "vite-plus/test";
import { landingCheckOptions, main } from "../src/cli.js";
import {
  checkLanding,
  companionLanding,
  createRenderer,
  headersFile,
  socialAssets,
  type Language,
  type LandingCheckOptions,
} from "../src/index.js";

const root = resolve(import.meta.dirname, "fixture-site");
const languages: readonly Language[] = [
  { code: "en", path: "/", label: "EN", nativeName: "English", ogLocale: "en_US" },
  { code: "it", path: "/it/", label: "IT", nativeName: "Italiano", ogLocale: "it_IT" },
];
const locales = Object.fromEntries(
  languages.map(({ code }) => [
    code,
    JSON.parse(readFileSync(join(root, `src/i18n/${code}.json`), "utf8")),
  ]),
);

function landing(overrides: Partial<Parameters<typeof companionLanding>[0]> = {}) {
  return companionLanding({
    root,
    siteUrl: "https://fixture.example",
    appStoreUrl: null,
    contactEmail: "hello@fixture.example",
    languages,
    defaultLanguageCode: "en",
    locales,
    legalPages: ["privacy"],
    placeholders: (key, language) => (key === "own" ? `own-${language.code}` : undefined),
    jsonLd: (language, locale) => ({
      name: "Fixture",
      inLanguage: language.code,
      description: locale.meta.description,
    }),
    og: {
      dir: join(root, "og"),
      iconPath: join(root, "public/favicon.svg"),
      fontFamily: "Fixture",
      iconBackground: "#FFF8E7",
    },
    ...overrides,
  });
}

/** Runs the page plugin's HTML hook the way Vite does for the file at `path`. */
function render(path: string, overrides = {}) {
  const plugin = landing(overrides).plugins[0] as any;
  const file = path.endsWith("/") ? `${path}index.html` : path;
  return plugin.transformIndexHtml.handler(readFileSync(join(root, `.${file}`), "utf8"), {
    path: file,
  });
}

describe("companionLanding", () => {
  it("renders every home page from the template, in its language", () => {
    const en = render("/") as string;
    const it_ = render("/it/") as string;
    expect(en).toContain("<title>Fixture</title>");
    expect(it_).toContain("<title>Prova</title>");
    expect(it_).toContain("Ciao own-it");
    expect(en).toContain('<meta property="og:locale" content="en_US" />');
    expect(en).toContain('<meta property="og:locale:alternate" content="it_IT" />');
    expect(en).toContain(
      '<link rel="alternate" hreflang="it" href="https://fixture.example/it/" />',
    );
    expect(en).toContain(
      '<link rel="alternate" hreflang="x-default" href="https://fixture.example/" />',
    );
    expect(it_).toContain('href="/it/privacy/#data"');
    expect(en).toContain('<script type="application/ld+json">{"name":"Fixture","inLanguage":"en"');
    expect(en).toContain("Coming soon");
    expect(en).toContain('aria-current="page"><span>English</span>');
  });

  it("puts the redirect script on the default page only", () => {
    expect(render("/")).toContain('localStorage.getItem("lang")');
    expect(render("/it/")).not.toContain("localStorage");
  });

  it("gives the legal pages their canonical and alternates", () => {
    const result = render("/it/privacy/") as {
      html: string;
      tags: { attrs: Record<string, string> }[];
    };
    expect(result.html).toContain("Niente lascia il tuo Mac.");
    expect(result.tags.map((tag) => tag.attrs)).toEqual([
      { rel: "canonical", href: "https://fixture.example/it/privacy/" },
      { rel: "alternate", hreflang: "en", href: "https://fixture.example/privacy/" },
      { rel: "alternate", hreflang: "it", href: "https://fixture.example/it/privacy/" },
      { rel: "alternate", hreflang: "x-default", href: "https://fixture.example/privacy/" },
    ]);
  });

  it("gives a page in one language its canonical alone", () => {
    const result = render("/compare/", { pages: [{ page: "compare", languages: ["en"] }] }) as {
      html: string;
      tags: { attrs: Record<string, string> }[];
    };
    expect(result.html).toContain("Fixture against the rest.");
    expect(result.tags.map((tag) => tag.attrs)).toEqual([
      { rel: "canonical", href: "https://fixture.example/compare/" },
    ]);
  });

  it("lists a page only in its languages", () => {
    const options = { pages: [{ page: "compare", languages: ["en"] }] };
    const plugin = landing(options).plugins[0] as any;
    const emitted: { fileName: string; source: string }[] = [];
    plugin.generateBundle.call({
      emitFile: (file: { fileName: string; source: string }) => emitted.push(file),
    });
    const sitemap = emitted.find((file) => file.fileName === "sitemap.xml")!.source;
    expect(sitemap).toContain("<url><loc>https://fixture.example/compare/</loc></url>");
    expect(sitemap).not.toContain("/it/compare/");
    expect(Object.keys(landing(options).build.rollupOptions.input)).toEqual([
      "main",
      "it",
      "privacy",
      "it-privacy",
      "compare",
      "notFound",
    ]);
  });

  it("refuses a page in a language the site doesn't have", () => {
    expect(() => landing({ pages: [{ page: "compare", languages: ["fr"] }] })).toThrow(
      "No language fr among the languages, for compare",
    );
  });

  it("switches languages in a row when asked", () => {
    expect(render("/it/", { languageSwitcher: "inline" })).toContain(
      '<nav class="languages" aria-label="Lingua"><a href="/" lang="en" hreflang="en" data-lang="en" title="English">EN</a>',
    );
  });

  const moreFrom = {
    apps: [
      { name: "Fixture", url: "https://fixture.example/" },
      {
        name: "Other <app>",
        url: 'https://other.example/?a=1&b="2"',
        tagline: (locale: any) => locale.footer.otherTagline,
      },
      { name: "Third", url: "https://third.example" },
    ],
    label: (locale: any) => locale.footer.moreFrom,
    builtOn: { url: "https://kit.example/", label: (locale: any) => locale.footer.builtOn },
  };

  it("lists the other apps and the kit under More from, in the page's language", () => {
    expect(render("/it/", { moreFrom })).toContain(
      '<footer><div class="more-from"><p class="more-from-label">Altro dall\'autore</p><ul class="more-from-apps">' +
        '<li><a href="https://other.example/?a=1&amp;b=&quot;2&quot;">Other &lt;app&gt;</a> <span class="more-from-tagline">Un\'altra prova</span></li>' +
        '<li><a href="https://third.example">Third</a></li></ul>' +
        '<p class="built-on"><a href="https://kit.example/">Fatto con un kit</a></p></div></footer>',
    );
  });

  it("renders More from as nothing without the option, or with nothing to list", () => {
    expect(render("/")).toContain("<footer></footer>");
    const self = { apps: [moreFrom.apps[0]!], label: moreFrom.label };
    expect(render("/", { moreFrom: self })).toContain("<footer></footer>");
    expect(render("/", { moreFrom: { ...self, builtOn: moreFrom.builtOn } })).toContain(
      '<footer><div class="more-from"><p class="built-on"><a href="https://kit.example/">Built on a kit</a></p></div></footer>',
    );
  });

  it("lists every page in the sitemap", () => {
    const plugin = landing().plugins[0] as any;
    const emitted: { fileName: string; source: string }[] = [];
    plugin.generateBundle.call({
      emitFile: (file: { fileName: string; source: string }) => emitted.push(file),
    });
    const sitemap = emitted.find((file) => file.fileName === "sitemap.xml")!.source;
    for (const url of ["/", "/it/", "/privacy/", "/it/privacy/"]) {
      expect(sitemap).toContain(`<loc>https://fixture.example${url}</loc>`);
    }
  });

  it("builds an input for every page", () => {
    expect(Object.keys(landing().build.rollupOptions.input)).toEqual([
      "main",
      "it",
      "privacy",
      "it-privacy",
      "notFound",
    ]);
  });

  it("leaves the social cards to the app without og", () => {
    const own = { name: "own-social-assets" };
    const names = landing({ og: undefined, extraPlugins: [own as any] }).plugins.map((p) => p.name);
    expect(names).toEqual(["laching-landing-html", "laching-landing-hosting", "own-social-assets"]);
    expect(landing().plugins.map((p) => p.name)).toContain("laching-landing-social-assets");
  });

  it("refuses locales that don't share their keys", () => {
    const broken = { ...locales, it: { ...locales.it, hero: {} } };
    expect(() => landing({ locales: broken })).toThrow("Missing in it.json: hero.title");
  });

  it("hashes each inline script in the CSP and fetches nothing", () => {
    const dir = mkdtempSync(join(tmpdir(), "landing-"));
    writeFileSync(
      join(dir, "index.html"),
      '<script>one()</script><script type="application/ld+json">{}</script><script src="/a.js"></script>',
    );
    writeFileSync(join(dir, "other.html"), "<script>two()</script>");
    const headers = headersFile(dir, { mediaSrc: "'self'" });
    rmSync(dir, { recursive: true });
    expect(headers.match(/'sha256-[^']+'/g)).toHaveLength(2);
    expect(headers).toContain("connect-src 'none'");
    expect(headers).toContain("media-src 'self'");
    expect(headers).toContain("/assets/*\n  ! Cache-Control");
  });

  it("renders the social cards and the icons as PNGs", () => {
    const files = socialAssets({
      ogDir: join(root, "og"),
      iconPath: join(root, "public/favicon.svg"),
      fontFamily: "Fixture",
      iconBackground: "#FFF8E7",
      copy: { en: { title: "Fixture card" }, it: { title: "Scheda di prova" } },
    });
    expect([...files.keys()].sort()).toEqual([
      "apple-touch-icon.png",
      "favicon-32.png",
      "icon-192.png",
      "icon-512.png",
      "og-en.png",
      "og-it.png",
    ]);
    for (const png of files.values()) expect(png.subarray(1, 4).toString()).toBe("PNG");
  });

  it("pads an icon whatever comes before its root tag, in linear time", () => {
    const { renderIcon } = createRenderer({
      ogDir: join(root, "og"),
      fontFamily: "Fixture",
      iconBackground: "#FFF8E7",
    });
    const icon = readFileSync(join(root, "public/favicon.svg"), "utf8");
    const padded = renderIcon(icon, 64, { padded: true });
    expect(
      renderIcon(`<?xml version="1.0" encoding="UTF-8"?>\n${icon}`, 64, { padded: true }),
    ).toEqual(padded);

    const start = performance.now();
    expect(() => renderIcon("<svg".repeat(100_000), 64, { padded: true })).toThrow("no <svg> tag");
    expect(() => renderIcon("<?xml".repeat(100_000), 64, { padded: true })).toThrow("no <svg> tag");
    expect(performance.now() - start).toBeLessThan(1000);
  });
});

describe("laching-landing check", () => {
  const scratch = mkdtempSync(join(tmpdir(), "landing-check-"));
  const dist = join(scratch, "dist");
  const options: LandingCheckOptions = {
    siteUrl: "https://fixture.example",
    languages,
    defaultLanguageCode: "en",
    legalPages: ["privacy"],
    contactEmail: "hello@fixture.example",
    stores: { apple: null },
    platforms: ["Mac"],
  };

  // The fixture built through its own vite.config.ts, the way a landing builds before the check.
  beforeAll(async () => {
    await build({ root, logLevel: "silent", build: { outDir: dist, emptyOutDir: true } });
  });
  afterAll(() => rmSync(scratch, { recursive: true, force: true }));

  /** A copy of the built fixture with `change` applied to it, checked. */
  function checkChanged(
    change: (copy: string) => void,
    overrides: Partial<LandingCheckOptions> = {},
  ) {
    const copy = mkdtempSync(join(scratch, "copy-"));
    cpSync(dist, copy, { recursive: true });
    change(copy);
    return checkLanding(copy, { ...options, ...overrides });
  }

  function edit(copy: string, file: string, from: string | RegExp, to: string) {
    const path = join(copy, file);
    const html = readFileSync(path, "utf8");
    const changed = html.replace(from, to);
    expect(changed).not.toBe(html);
    writeFileSync(path, changed);
  }

  it("passes the built fixture, from its Vite config", async () => {
    const printed: string[] = [];
    expect(await main(["check", dist], root, (line) => printed.push(line))).toBe(0);
    expect(printed).toEqual([`${dist} passes`]);
    expect(checkLanding(dist, options)).toEqual([]);
  });

  it("carries companionLanding's options for the check", async () => {
    const plugins = landing({ stores: { apple: null }, platforms: ["Mac"] }).plugins;
    expect(await landingCheckOptions([[], plugins])).toMatchObject({
      siteUrl: "https://fixture.example",
      legalPages: ["privacy"],
      contactEmail: "hello@fixture.example",
      stores: { apple: null },
      platforms: ["Mac"],
    });
  });

  it("changes no page with the check's options", () => {
    const checked = { stores: { apple: null, google: null }, platforms: ["Mac"] };
    for (const path of ["/", "/it/", "/it/privacy/"])
      expect(render(path, checked)).toEqual(render(path));
  });

  it("refuses anything but check, and a missing build", async () => {
    const printed: string[] = [];
    expect(await main(["build"], root, (line) => printed.push(line))).toBe(2);
    expect(await main(["check", join(scratch, "none")], root, (line) => printed.push(line))).toBe(
      2,
    );
    expect(await main(["check", "--bogus"], root, (line) => printed.push(line))).toBe(2);
    expect(await main(["check", "--config", "nope.ts"], root, (line) => printed.push(line))).toBe(
      2,
    );
    expect(printed.slice(0, 3)).toEqual([
      "Usage: laching-landing check [dist] [--config vite.config.ts]",
      `No built site at ${join(scratch, "none")}: build it first.`,
      "Usage: laching-landing check [dist] [--config vite.config.ts]",
    ]);
    expect(printed[3]).toMatch(/^Can't load nope\.ts: /);
  });

  it("leaves an out-of-range numeric entity as written", () => {
    expect(
      checkChanged((copy) =>
        edit(copy, "index.html", "<footer>", "<footer><p>&#x110000; &#99999999;</p>"),
      ),
    ).toEqual([]);
  });

  it("fails on a dead link and a dead anchor", () => {
    expect(
      checkChanged((copy) => {
        edit(copy, "it/index.html", "<footer>", '<footer><a href="/it/terms/">Termini</a>');
        edit(copy, "index.html", 'href="/privacy/#data"', 'href="/privacy/#nowhere"');
        edit(copy, "privacy/index.html", "<body>", '<body><img src="../missing.png" />');
      }),
    ).toEqual([
      '/: dead anchor /privacy/#nowhere (no id "nowhere" on /privacy/)',
      "/it/: dead link /it/terms/",
      "/privacy/: dead link ../missing.png",
    ]);
  });

  it("fails on a one-way hreflang and a canonical that isn't the page", () => {
    expect(
      checkChanged((copy) => {
        edit(copy, "privacy/index.html", /<link rel="alternate" hreflang="it"[^>]*>/, "");
        edit(
          copy,
          "it/index.html",
          'rel="canonical" href="https://fixture.example/it/"',
          'rel="canonical" href="https://fixture.example/"',
        );
      }),
    ).toEqual([
      "/it/: canonical https://fixture.example/ isn't the page itself",
      "/it/privacy/: one-way hreflang en (/privacy/ doesn't link back)",
    ]);
  });

  it("fails on a legal page missing in one language", () => {
    const problems = checkChanged((copy) => rmSync(join(copy, "it/privacy"), { recursive: true }));
    expect(problems).toContain("it: no privacy page at /it/privacy/");
    expect(problems).toContain("/it/: dead link /it/privacy/#data");
  });

  it("fails on a home page with no way to reach support", () => {
    expect(
      checkChanged((copy) =>
        edit(copy, "it/index.html", "mailto:hello@fixture.example", "mailto:other@fixture.example"),
      ),
    ).toEqual(["/it/: no contact link to hello@fixture.example and no support page"]);
    expect(checkChanged(() => {}, { legalPages: ["privacy", "support"] })).toEqual([
      "en: no support page at /support/",
      "it: no support page at /it/support/",
    ]);
  });

  it("fails on a legal page that doesn't name a configured store or platform, unless an alias does", () => {
    expect(
      checkChanged(() => {}, {
        stores: { apple: null, microsoft: null },
        platforms: ["Mac", "Windows"],
      }),
    ).toEqual([
      "/privacy/: never names Microsoft Store, Windows",
      "/it/privacy/: never names Microsoft Store, Windows",
    ]);
    expect(
      checkChanged(() => {}, {
        platforms: ["macOS"],
        nameAliases: { en: { macOS: ["Mac"] }, it: { macOS: ["Mac"] } },
      }),
    ).toEqual([]);
  });
});
