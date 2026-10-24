import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vite-plus/test";
import {
  companionLanding,
  createRenderer,
  headersFile,
  socialAssets,
  type Language,
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
    expect(it_).toContain('href="/it/privacy/"');
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
