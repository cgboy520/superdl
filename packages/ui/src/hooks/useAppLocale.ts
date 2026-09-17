/** antd locale / dayjs locale / html lang / document.title 四联动出口(两端共用);relativeTime 插件在此 extend。 */
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import relativeTime from "dayjs/plugin/relativeTime";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

dayjs.extend(relativeTime);

const ANTD_LOCALES = { "zh-CN": zhCN, "en-US": enUS } as const;
const DAYJS_LOCALES = { "zh-CN": "zh-cn", "en-US": "en" } as const;

export function useAppLocale() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage === "zh-CN" ? "zh-CN" : "en-US";
  useEffect(() => {
    dayjs.locale(DAYJS_LOCALES[lang]);
    document.documentElement.lang = lang;
    document.title = t("app.title");
  }, [lang, t]);
  return ANTD_LOCALES[lang];
}
