/**
 * i18n 运行时初始化(main.tsx 顶部副作用引入,先于首次 render)。
 * 语言探测:localStorage("superdl.lang") → navigator;缺译回落 zh-CN(基准语言)。
 */
import errorsEn from "@superdl/ui/locales/en-US/errors.json";
import sharedEn from "@superdl/ui/locales/en-US/shared.json";
import errorsZh from "@superdl/ui/locales/zh-CN/errors.json";
import sharedZh from "@superdl/ui/locales/zh-CN/shared.json";
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enUS from "./locales/en-US/web.json";
import zhCN from "./locales/zh-CN/web.json";

export const SUPPORTED_LANGS = ["zh-CN", "en-US"] as const;
export type AppLang = (typeof SUPPORTED_LANGS)[number];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      "zh-CN": { web: zhCN, shared: sharedZh, errors: errorsZh },
      "en-US": { web: enUS, shared: sharedEn, errors: errorsEn },
    },
    fallbackLng: "zh-CN",
    supportedLngs: [...SUPPORTED_LANGS],
    defaultNS: "web",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "superdl.lang",
    },
    returnNull: false,
  });

export default i18n;
