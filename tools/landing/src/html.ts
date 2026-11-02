import type { Language, Locale } from "./types.js";
import { readPath } from "./locales.js";

export function escapeAttr(value: string) {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function escapeHtml(value: string) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** A value inlined into a `<script>`: `<` escaped so the data can't close the tag. */
export function scriptJson(value: unknown) {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

/** The Apple logo, for a Mac App Store button. */
export const appleIcon =
  '<svg class="store-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701"/></svg>';

const globeIcon =
  '<svg class="globe" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"/></svg>';

/** A `<details>` dropdown behind a globe: every language by native name and code. Nothing with one language. */
export function menuLanguageSwitcher(
  languages: readonly Language[],
  current: Language,
  label: string,
) {
  if (languages.length < 2) return "";
  const items = languages
    .map(
      (language) =>
        `<li><a href="${language.path}" lang="${language.code}" hreflang="${language.code}" data-lang="${language.code}"${
          language.code === current.code ? ' aria-current="page"' : ""
        }><span>${language.nativeName}</span><span class="language-code">${language.label}</span></a></li>`,
    )
    .join("");
  return `<details class="language-switcher"><summary aria-label="${escapeAttr(
    `${label}: ${current.nativeName}`,
  )}" title="${escapeAttr(label)}">${globeIcon}<span>${
    current.label
  }</span><svg class="caret" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="M2.5 4.5 6 8l3.5-3.5"/></svg></summary><ul class="language-menu">${items}</ul></details>`;
}

/** The codes in a row (`<nav class="languages">`), each titled with its native name. Nothing with one language. */
export function inlineLanguageSwitcher(
  languages: readonly Language[],
  current: Language,
  label: string,
) {
  if (languages.length < 2) return "";
  const items = languages
    .map(
      (language) =>
        `<a href="${language.path}" lang="${language.code}" hreflang="${language.code}" data-lang="${language.code}" title="${escapeAttr(
          language.nativeName,
        )}"${language.code === current.code ? ' aria-current="page"' : ""}>${language.label}</a>`,
    )
    .join("");
  return `<nav class="languages" aria-label="${escapeAttr(label)}">${items}</nav>`;
}

/**
 * On the default page only: sends a first-time visitor to their browser's preferred language (or an alias of one),
 * once, skipping the tags a language says it isn't for.
 */
export function langRedirectScript(
  languages: readonly Language[],
  defaultLanguage: Language,
  language: Language,
) {
  if (language !== defaultLanguage || languages.length < 2) return "";
  const others = languages.filter((candidate) => candidate !== defaultLanguage);
  const paths = Object.fromEntries(
    others.flatMap((other) =>
      [other.code, ...(other.aliases ?? [])].map((tag) => [tag.toLowerCase(), other.path]),
    ),
  );
  // An empty skip list costs ~20 bytes of inline script and keeps one template.
  const skip = others.flatMap((other) => other.notFor ?? []);
  return `<script>try { if (!localStorage.getItem("lang")) { const paths = ${JSON.stringify(
    paths,
  )}; const skip = ${JSON.stringify(
    skip,
  )}; const wanted = (navigator.languages?.[0] || navigator.language || "").toLowerCase(); const base = wanted.split("-")[0]; const path = skip.some((tag) => wanted.startsWith(tag)) ? undefined : paths[wanted] || paths[base] || Object.entries(paths).find(([code]) => code.split("-")[0] === base)?.[1]; if (path) location.replace(path + location.hash); } } catch {}</script>`;
}

/**
 * The Mac App Store button (`.button`, `.button-big` when `big`), or "coming soon" (`locale.store.soon`) while there's
 * no listing; `locale.store.mac` labels the link.
 */
export function macStoreButton(appStoreUrl: string | null, locale: Locale, big: boolean) {
  const size = big ? " button-big" : "";
  if (!appStoreUrl) {
    return `<span class="button button-soon${size}">${appleIcon}<span>${escapeHtml(
      locale.store.soon,
    )}</span></span>`;
  }
  return `<a class="button${size}" href="${escapeAttr(appStoreUrl)}">${appleIcon}<span>${escapeHtml(
    locale.store.mac,
  )}</span></a>`;
}

/** One sibling app in a "More from" list, its labels already in the page's language. */
export interface MoreFromApp {
  name: string;
  url: string;
  tagline?: string;
}

/**
 * A "More from" list (`.more-from`): `label`, then each app linked by name (`.more-from-apps`, an optional
 * `.more-from-tagline` after it), then an optional "Built on" link (`.built-on`). Everything escaped, no styles;
 * nothing when there's neither an app nor a "Built on" link.
 */
export function moreFromList(
  label: string,
  apps: readonly MoreFromApp[],
  builtOn?: { url: string; label: string },
) {
  if (!apps.length && !builtOn) return "";
  const items = apps
    .map(
      ({ name, url, tagline }) =>
        `<li><a href="${escapeAttr(url)}">${escapeHtml(name)}</a>${
          tagline ? ` <span class="more-from-tagline">${escapeHtml(tagline)}</span>` : ""
        }</li>`,
    )
    .join("");
  const list = apps.length
    ? `<p class="more-from-label">${escapeHtml(label)}</p><ul class="more-from-apps">${items}</ul>`
    : "";
  const built = builtOn
    ? `<p class="built-on"><a href="${escapeAttr(builtOn.url)}">${escapeHtml(builtOn.label)}</a></p>`
    : "";
  return `<div class="more-from">${list}${built}</div>`;
}

/**
 * Fills `{{ key }}` from `generated` (its own keys) or the locale (dotted keys); throws on a missing key, a key that
 * isn't a scalar, or anything left unresolved.
 */
export function renderTemplate(
  html: string,
  languageCode: string,
  locale: Locale,
  generated: (key: string) => string | undefined,
) {
  const rendered = html.replace(/{{\s*([\w.-]+)\s*}}/g, (_placeholder, key: string) => {
    const value = generated(key);
    if (value !== undefined) return value;

    const localized = readPath(locale, key);
    if (localized === undefined) {
      throw new Error(`Missing i18n key "${key}" for locale "${languageCode}"`);
    }
    if (typeof localized === "object") {
      throw new Error(`i18n key "${key}" for locale "${languageCode}" must be a scalar`);
    }
    return String(localized);
  });

  const unresolved = rendered.match(/{{[^}]+}}/g);
  if (unresolved) {
    throw new Error(`Unresolved i18n placeholders for ${languageCode}: ${unresolved.join(", ")}`);
  }

  return rendered;
}
