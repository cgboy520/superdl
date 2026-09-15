import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { makeFormatters, type SharedT } from "../format";

export function useFormat() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  return useMemo(() => makeFormatters(t as unknown as SharedT, locale), [t, locale]);
}
