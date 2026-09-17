/** Shared i18n bootstrap for both consoles: `shared` / `errors` come from ui; language detection is
 *  localStorage("superdl.lang") → navigator, and missing translations fall back to en-US. */

import i18n, { type i18n as I18nInstance, type Resource } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import errorsEn from "../locales/en-US/errors.json";
import sharedEn from "../locales/en-US/shared.json";
import errorsZh from "../locales/zh-CN/errors.json";
import sharedZh from "../locales/zh-CN/shared.json";

export const SUPPORTED_LANGS = ["zh-CN", "en-US"] as const;
export type AppLang = (typeof SUPPORTED_LANGS)[number];

export function initAppI18n(opts: {
  /** Default ns of the console ("web" / "admin") */
  appNs: string;
  appResources: { "zh-CN": unknown; "en-US": unknown };
}): I18nInstance {
  const resources = {
    "zh-CN": { [opts.appNs]: opts.appResources["zh-CN"], shared: sharedZh, errors: errorsZh },
    "en-US": { [opts.appNs]: opts.appResources["en-US"], shared: sharedEn, errors: errorsEn },
  } as Resource;
  void i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources,
      fallbackLng: "en-US",
      supportedLngs: [...SUPPORTED_LANGS],
      defaultNS: opts.appNs,
      interpolation: { escapeValue: false },
      detection: {
        order: ["localStorage", "navigator"],
        caches: ["localStorage"],
        lookupLocalStorage: "superdl.lang",
      },
      returnNull: false,
    });
  return i18n;
}

export default i18n;
