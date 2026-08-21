/**
 * 管理端 i18n 最小运行时:先只挂 shared/errors 目录供状态徽标等共享件 t() 解析。
 * 语言探测/切换器与 admin namespace 在 WP24 C12 接入,当前管理端恒中文。
 */
import sharedEn from "@superdl/ui/locales/en-US/shared.json";
import sharedZh from "@superdl/ui/locales/zh-CN/shared.json";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

void i18n.use(initReactI18next).init({
  resources: {
    "zh-CN": { shared: sharedZh },
    "en-US": { shared: sharedEn },
  },
  lng: "zh-CN",
  fallbackLng: "zh-CN",
  defaultNS: "shared",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
