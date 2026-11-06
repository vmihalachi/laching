import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { legalPath } from "./legal.js";
import type { Language } from "./types.js";

/**
 * The stores the app ships in, each its listing's URL or null while there's none. The check looks for each store's
 * own name ("App Store", "Microsoft Store", "Google Play"), which the stores ask never to be translated.
 */
export interface LandingStores {
  apple?: string | null;
  microsoft?: string | null;
  google?: string | null;
}

const storeNames: Record<keyof LandingStores, string> = {
  apple: "App Store",
  microsoft: "Microsoft Store",
  google: "Google Play",
};

/** What `checkLanding` needs to know about a landing: `companionLanding`'s options of the same names. */
export interface LandingCheckOptions {
  siteUrl: string;
  languages: readonly Language[];
  defaultLanguageCode: string;
  /** `["privacy", "terms"]` when left out. A "support" page among them stands in for the home pages' contact link. */
  legalPages?: readonly string[];
  pages?: readonly { page: string; languages?: readonly string[] }[];
  /** The address every home page's contact link (`mailto:`) must reach; any `mailto:` link when left out. */
  contactEmail?: string;
  /** Each one's name must appear on every legal page, in every language. */
  stores?: LandingStores;
  /** The platforms' names as the legal pages write them ("Mac", "Windows"); each must appear on every legal page. */
  platforms?: readonly string[];
  /** Another way a language writes a store's or platform's name: language code → name → its spellings. */
  nameAliases?: Record<string, Record<string, readonly string[]>>;
}

interface Tag {
  name: string;
  attrs: Record<string, string>;
}

interface Page {
  /** The page's URL path: "/it/privacy/", "/404.html". */
  path: string;
  tags: Tag[];
  ids: Set<string>;
  text: string;
}

function decodeEntities(value: string) {
  return value.replace(
    /&(#x[0-9a-f]+|#\d+|amp|quot|apos|lt|gt|nbsp);/gi,
    (entity, code: string) => {
      const lower = code.toLowerCase();
      if (lower.startsWith("#")) {
        // An out-of-range number (past U+10FFFF) is left as written rather than thrown on.
        const point = lower.startsWith("#x")
          ? parseInt(lower.slice(2), 16)
          : parseInt(lower.slice(1), 10);
        return point <= 0x10ffff ? String.fromCodePoint(point) : entity;
      }
      return { amp: "&", quot: '"', apos: "'", lt: "<", gt: ">", nbsp: " " }[lower] ?? entity;
    },
  );
}

/**
 * The page with its comments left out and its scripts' and styles' bodies and end tags emptied, in one pass from the
 * start, so a comment inside a script (or a script inside a comment) ends where a browser ends it.
 */
function withoutRawText(html: string) {
  const opening = /<!--|<(script|style)(?=[\s/>])[^>]*>/gi;
  let kept = "";
  let from = 0;
  for (let match = opening.exec(html); match; match = opening.exec(html)) {
    const element = match[1];
    kept += html.slice(from, element ? opening.lastIndex : match.index);
    from = element ? rawTextEnd(html, opening.lastIndex, element) : commentEnd(html, match.index);
    opening.lastIndex = from;
  }
  return kept + html.slice(from);
}

/** Past the end of the comment opened at `start`: its `-->` (`<!-->` and `<!--->` end at once), or the page's end. */
function commentEnd(html: string, start: number) {
  const end = html.indexOf("-->", start + 2);
  return end < 0 ? html.length : end + 3;
}

/**
 * Past the end tag of the script's or style's body starting at `from`: `</script` followed by a space, `/` or `>`,
 * up to the next `>` (whatever junk the tag carries, `</script\n bar>` included), or the page's end.
 */
function rawTextEnd(html: string, from: number, element: string) {
  const closing = new RegExp(`</${element}(?=[\\s/>]|$)[^>]*>?`, "gi");
  closing.lastIndex = from;
  return closing.exec(html) ? closing.lastIndex : html.length;
}

/** The tags, ids and text of an HTML page, with comments and the bodies of scripts and styles left out. */
function parsePage(path: string, html: string): Page {
  const stripped = withoutRawText(html);
  const tags: Tag[] = [];
  const ids = new Set<string>();
  for (const [, name, rest] of stripped.matchAll(/<([a-zA-Z][\w-]*)\b([^>]*)>/g)) {
    const attrs: Record<string, string> = {};
    for (const [, key, double, single, bare] of rest.matchAll(
      /([^\s=/"'>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g,
    )) {
      attrs[key.toLowerCase()] = decodeEntities(double ?? single ?? bare ?? "");
    }
    tags.push({ name: name.toLowerCase(), attrs });
    if (attrs.id) ids.add(attrs.id);
    if (name.toLowerCase() === "a" && attrs.name) ids.add(attrs.name);
  }
  const text = decodeEntities(stripped.replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ");
  return { path, tags, ids, text };
}

function htmlFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return htmlFiles(path);
    return entry.name.endsWith(".html") ? [path] : [];
  });
}

/** The URL path a built file is served at: `it/privacy/index.html` → "/it/privacy/". */
function urlPath(dist: string, file: string) {
  const path = `/${relative(dist, file).split(sep).join("/")}`;
  return path.endsWith("/index.html") ? path.slice(0, -"index.html".length) : path;
}

/** The built file a URL path is served from, the way Cloudflare Pages finds it, if any. */
function fileFor(dist: string, path: string) {
  const local = join(dist, ...path.split("/").filter(Boolean));
  const candidates = path.endsWith("/")
    ? [join(local, "index.html")]
    : [local, `${local}.html`, join(local, "index.html")];
  return candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile());
}

function decoded(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function linkRels(tag: Tag) {
  return (tag.attrs.rel ?? "").toLowerCase().split(/\s+/);
}

/**
 * Checks a built landing in `dist` offline, and returns its problems (none when it passes):
 * 1. every internal `href`, `src` and `srcset` resolves to a file in `dist`, and every `#fragment` to an id on the
 *    page it points to;
 * 2. every hreflang set is reciprocal, names its own page and has one `x-default`, a page with one has a canonical,
 *    and each canonical is the page itself;
 * 3. every home page, legal page and other page exists in each of its languages, and without a "support" page, every
 *    home page links its contact address;
 * 4. with `stores` or `platforms`, every legal page in every language names each of them (or its alias there).
 */
export function checkLanding(dist: string, options: LandingCheckOptions): string[] {
  const problems: string[] = [];
  const site = new URL(options.siteUrl);
  const pages = new Map(
    htmlFiles(dist).map((file) => {
      const path = urlPath(dist, file);
      return [file, parsePage(path, readFileSync(file, "utf8"))] as const;
    }),
  );
  const pageAt = (path: string) => {
    const file = fileFor(dist, path);
    return file === undefined ? undefined : pages.get(file);
  };
  /** The site path a reference on `page` points to, or undefined when it leaves the site. */
  const internal = (reference: string, page: Page) => {
    if (/^(mailto|tel|javascript|data|sms):/i.test(reference.trim())) return undefined;
    let url: URL;
    try {
      url = new URL(reference.trim(), new URL(page.path, site));
    } catch {
      return undefined;
    }
    return url.origin === site.origin ? url : undefined;
  };

  // 1. Links and anchors.
  for (const page of pages.values()) {
    for (const tag of page.tags) {
      if (
        tag.name === "link" &&
        linkRels(tag).some((rel) => rel === "alternate" || rel === "canonical")
      )
        continue;
      const references = [
        tag.attrs.href,
        tag.attrs.src,
        ...(tag.attrs.srcset ?? "")
          .split(",")
          .map((candidate) => candidate.trim().split(/\s+/)[0])
          .filter(Boolean),
      ].filter((reference): reference is string => reference !== undefined && reference !== "");
      for (const reference of references) {
        const url = internal(reference, page);
        if (!url) continue;
        const file = fileFor(dist, decoded(url.pathname));
        if (file === undefined) {
          problems.push(`${page.path}: dead link ${reference}`);
          continue;
        }
        const fragment = decoded(url.hash.slice(1));
        if (!fragment || fragment === "top") continue;
        const target = pages.get(file);
        if (target && !target.ids.has(fragment))
          problems.push(
            `${page.path}: dead anchor ${reference} (no id "${fragment}" on ${target.path})`,
          );
      }
    }
  }

  // 2. hreflang and canonicals.
  const sets = new Map<Page, Map<string, string>>();
  for (const page of pages.values()) {
    const own = new URL(page.path, site).href;
    const links = page.tags.filter((tag) => tag.name === "link");
    const canonicals = links.filter((tag) => linkRels(tag).includes("canonical"));
    const alternates = links.filter(
      (tag) => linkRels(tag).includes("alternate") && tag.attrs.hreflang,
    );
    if (canonicals.length > 1) problems.push(`${page.path}: ${canonicals.length} canonical links`);
    for (const canonical of canonicals) {
      const href = canonical.attrs.href ?? "";
      if (internal(href, page)?.href !== own)
        problems.push(`${page.path}: canonical ${href} isn't the page itself`);
    }
    if (!alternates.length) continue;
    if (!canonicals.length) problems.push(`${page.path}: hreflang links but no canonical`);
    const set = new Map<string, string>();
    for (const { attrs } of alternates) {
      const hreflang = attrs.hreflang!.toLowerCase();
      const href = internal(attrs.href ?? "", page)?.href ?? attrs.href ?? "";
      if (set.has(hreflang) && set.get(hreflang) !== href)
        problems.push(
          `${page.path}: hreflang ${hreflang} points to both ${set.get(hreflang)} and ${href}`,
        );
      set.set(hreflang, href);
    }
    const xDefaults = alternates.filter(
      ({ attrs }) => attrs.hreflang!.toLowerCase() === "x-default",
    );
    if (xDefaults.length !== 1)
      problems.push(`${page.path}: ${xDefaults.length} x-default hreflang links`);
    if (![...set].some(([hreflang, href]) => hreflang !== "x-default" && href === own))
      problems.push(`${page.path}: its hreflang links leave out the page itself`);
    sets.set(page, set);
  }
  for (const [page, set] of sets) {
    const own = new URL(page.path, site).href;
    for (const [hreflang, href] of set) {
      if (hreflang === "x-default" || href === own) continue;
      const url = internal(href, page);
      if (!url) continue;
      const target = pageAt(url.pathname);
      if (!target) {
        problems.push(`${page.path}: hreflang ${hreflang} points to a missing page, ${href}`);
        continue;
      }
      const theirs = sets.get(target);
      if (![...(theirs ?? [])].some(([code, back]) => code !== "x-default" && back === own))
        problems.push(
          `${page.path}: one-way hreflang ${hreflang} (${target.path} doesn't link back)`,
        );
      else if (
        theirs!.size !== set.size ||
        [...set].some(([code, value]) => theirs!.get(code) !== value)
      )
        problems.push(`${page.path}: its hreflang set differs from ${target.path}'s`);
    }
  }

  // 3. Every page in every language, and a way to reach support.
  const legalPages = options.legalPages ?? ["privacy", "terms"];
  const languageFor = (code: string) =>
    options.languages.find((language) => language.code === code);
  const expected: { page: string; languages: readonly Language[] }[] = [
    ...legalPages.map((page) => ({ page, languages: options.languages })),
    ...(options.pages ?? []).map(({ page, languages: codes }) => ({
      page,
      languages: codes ? codes.flatMap((code) => languageFor(code) ?? []) : options.languages,
    })),
  ];
  for (const language of options.languages) {
    const home = pageAt(language.path);
    if (!home) {
      problems.push(`${language.code}: no home page at ${language.path}`);
      continue;
    }
    if (legalPages.includes("support")) continue;
    const contact = home.tags.some(({ name, attrs }) => {
      if (name !== "a" || !/^mailto:/i.test(attrs.href ?? "")) return false;
      const address = decoded(attrs.href!.slice("mailto:".length).split("?")[0]!);
      return !options.contactEmail || address.toLowerCase() === options.contactEmail.toLowerCase();
    });
    if (!contact)
      problems.push(
        `${language.path}: no contact link${options.contactEmail ? ` to ${options.contactEmail}` : ""} and no support page`,
      );
  }
  for (const { page, languages } of expected) {
    for (const language of languages) {
      if (!pageAt(legalPath(language, page)))
        problems.push(`${language.code}: no ${page} page at ${legalPath(language, page)}`);
    }
  }

  // 4. The stores and platforms, named on every legal page in every language.
  const names = [
    ...(Object.keys(storeNames) as (keyof LandingStores)[])
      .filter((store) => options.stores?.[store] !== undefined)
      .map((store) => storeNames[store]),
    ...(options.platforms ?? []),
  ];
  for (const language of names.length ? options.languages : []) {
    for (const legal of legalPages) {
      const page = pageAt(legalPath(language, legal));
      if (!page) continue;
      const missing = names.filter(
        (name) =>
          ![name, ...(options.nameAliases?.[language.code]?.[name] ?? [])].some((spelling) =>
            page.text.includes(spelling),
          ),
      );
      if (missing.length) problems.push(`${page.path}: never names ${missing.join(", ")}`);
    }
  }

  return problems;
}
