import { alternates, legalPath, xDefaultLanguage, type PageLanguages } from "./legal.js";
import type { Language } from "./types.js";

/**
 * Every page (home and legal) in every language, and the other hand-written pages in theirs, each with its
 * alternates.
 */
export function renderSitemap(
  siteUrl: string,
  languages: readonly Language[],
  defaultLanguage: Language,
  legalPages: readonly string[],
  pages: readonly PageLanguages[] = [],
) {
  const entries: { languages: readonly Language[]; pathFor: (language: Language) => string }[] = [
    { languages, pathFor: ({ path }) => path },
    ...[...legalPages.map((page) => ({ page, languages })), ...pages].map((entry) => ({
      languages: entry.languages,
      pathFor: (language: Language) => legalPath(language, entry.page),
    })),
  ];
  const urls = entries
    .flatMap(({ languages: pageLanguages, pathFor }) => {
      // A page in one language has no translations to point to.
      if (pageLanguages.length === 1)
        return [`<url><loc>${siteUrl}${pathFor(pageLanguages[0]!)}</loc></url>`];
      const xDefault = xDefaultLanguage(pageLanguages, defaultLanguage);
      const links = alternates(siteUrl, pageLanguages, xDefault, pathFor)
        .map(
          ([hreflang, href]) =>
            `<xhtml:link rel="alternate" hreflang="${hreflang}" href="${href}"/>`,
        )
        .join("");
      return pageLanguages.map(
        (language) => `<url><loc>${siteUrl}${pathFor(language)}</loc>${links}</url>`,
      );
    })
    .join("\n  ");
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  ${urls}
</urlset>
`;
}
