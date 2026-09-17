import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { makeFormatters, type SharedT } from "../format";
import { useCurrency } from "./useCurrency";

export function useFormat() {
  const { t, i18n } = useTranslation();
  const currency = useCurrency();
  const locale = i18n.resolvedLanguage === "zh-CN" ? "zh-CN" : "en-US";
  return useMemo(() => makeFormatters(t as unknown as SharedT, locale, currency), [t, locale, currency]);
}
