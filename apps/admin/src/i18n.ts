/**
 * 管理端 i18n 运行时:admin ns + 共享 shared/errors 目录。
 * 语言探测同 web(localStorage "superdl.lang" → navigator),fallback zh-CN。
 */
import errorsEn from "@superdl/ui/locales/en-US/errors.json";
import sharedEn from "@superdl/ui/locales/en-US/shared.json";
import errorsZh from "@superdl/ui/locales/zh-CN/errors.json";
import sharedZh from "@superdl/ui/locales/zh-CN/shared.json";
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import adminEn from "./locales/en-US/admin.json";
import adminZh from "./locales/zh-CN/admin.json";

export const SUPPORTED_LANGS = ["zh-CN", "en-US"] as const;
export type AppLang = (typeof SUPPORTED_LANGS)[number];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      "zh-CN": { admin: adminZh, shared: sharedZh, errors: errorsZh },
      "en-US": { admin: adminEn, shared: sharedEn, errors: errorsEn },
    },
    fallbackLng: "zh-CN",
    supportedLngs: [...SUPPORTED_LANGS],
    defaultNS: "admin",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "superdl.lang",
    },
    returnNull: false,
  })
  .then(() => syncHtmlLang(i18n.language));

// <html lang> 跟随运行时语言;index.html 的 zh-CN 只是 JS 启动前的静态默认
function syncHtmlLang(lng: string): void {
  document.documentElement.lang = lng;
}
i18n.on("languageChanged", syncHtmlLang);

export default i18n;
