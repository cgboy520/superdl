/** 把 makeFormatters 绑定到当前语言。 */
import { makeFormatters, type SharedT } from "@superdl/ui";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

export function useFormat() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  return useMemo(() => makeFormatters(t as SharedT, locale), [t, locale]);
}
