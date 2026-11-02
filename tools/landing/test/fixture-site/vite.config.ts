import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite-plus";
import { companionLanding } from "../../src/index.js";

// The fixture landing as `laching-landing check` loads it: the tests build it with these options and their own.
const languages = [
  { code: "en", path: "/", label: "EN", nativeName: "English", ogLocale: "en_US" },
  { code: "it", path: "/it/", label: "IT", nativeName: "Italiano", ogLocale: "it_IT" },
];

export default defineConfig({
  ...companionLanding({
    root: __dirname,
    siteUrl: "https://fixture.example",
    appStoreUrl: null,
    contactEmail: "hello@fixture.example",
    languages,
    defaultLanguageCode: "en",
    locales: Object.fromEntries(
      languages.map(({ code }) => [
        code,
        JSON.parse(readFileSync(resolve(__dirname, `src/i18n/${code}.json`), "utf8")),
      ]),
    ),
    legalPages: ["privacy"],
    placeholders: (key, language) => (key === "own" ? `own-${language.code}` : undefined),
    jsonLd: (language) => ({ name: "Fixture", inLanguage: language.code }),
    stores: { apple: null },
    platforms: ["Mac"],
  }),
});
