/** antd ConfigProvider locale / dayjs locale / html lang / document.title 四联动的唯一出口(两端共用)。
 *  relativeTime 插件在此统一 extend(幂等):管理端 fromNow 依赖它,原先由 admin lib/locale.ts 副作用引入。 */
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
  const lang = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  useEffect(() => {
    dayjs.locale(DAYJS_LOCALES[lang]);
    document.documentElement.lang = lang;
    document.title = t("app.title");
  }, [lang, t]);
  return ANTD_LOCALES[lang];
}
