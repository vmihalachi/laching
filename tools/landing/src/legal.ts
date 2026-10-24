import type { Language } from "./types.js";

/** A legal page's path in a language: `/de/privacy/`. */
export function legalPath(language: Language, page: string) {
  return `${language.path}${page}/`;
}

/** The legal page a built HTML file is (`/de/privacy/index.html` → German privacy), if any. */
export function legalPageForPath(
  languages: readonly Language[],
  pages: readonly string[],
  pathname: string,
) {
  for (const language of languages) {
    for (const page of pages) {
      const path = legalPath(language, page);
      if (pathname === path || pathname === `${path}index.html`) return { language, page };
    }
  }
  return undefined;
}

/** A hand-written page beyond the legal ones and the languages it's written in. */
export interface PageLanguages<L extends Language = Language> {
  page: string;
  languages: readonly L[];
}

/** The hand-written page a built HTML file is (`/de/compare/index.html` → German compare), if any. */
export function pageForPath<L extends Language>(
  pages: readonly PageLanguages<L>[],
  pathname: string,
) {
  for (const entry of pages) {
    for (const language of entry.languages) {
      const path = legalPath(language, entry.page);
      if (pathname === path || pathname === `${path}index.html`) return { language, entry };
    }
  }
  return undefined;
}

/** x-default for a page in these languages: the site's default when it's among them, else the first. */
export function xDefaultLanguage<L extends Language>(languages: readonly L[], defaultLanguage: L) {
  return languages.includes(defaultLanguage) ? defaultLanguage : languages[0]!;
}

/** hreflang → absolute URL of one page in every language, plus x-default. */
export function alternates(
  siteUrl: string,
  languages: readonly Language[],
  defaultLanguage: Language,
  pathFor: (language: Language) => string,
): [string, string][] {
  return [
    ...languages.map((language): [string, string] => [
      language.code,
      `${siteUrl}${pathFor(language)}`,
    ]),
    ["x-default", `${siteUrl}${pathFor(defaultLanguage)}`],
  ];
}
