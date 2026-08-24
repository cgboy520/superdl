/** antd ConfigProvider locale / dayjs locale / html lang / document.title 四联动的唯一出口。 */
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import type { AppLang } from "../i18n";

const ANTD_LOCALES = { "zh-CN": zhCN, "en-US": enUS } as const;
const DAYJS_LOCALES = { "zh-CN": "zh-cn", "en-US": "en" } as const;

export function useAppLocale() {
  const { t, i18n } = useTranslation();
  const lang: AppLang = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  useEffect(() => {
    dayjs.locale(DAYJS_LOCALES[lang]);
    document.documentElement.lang = lang;
    document.title = t("app.title");
  }, [lang, t]);
  return ANTD_LOCALES[lang];
}
