/** makeFormatters 绑定到当前语言(两端共用)。 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { makeFormatters, type SharedT } from "../format";

export function useFormat() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  // typed-t → SharedT 的 cast 点
  return useMemo(() => makeFormatters(t as unknown as SharedT, locale), [t, locale]);
}
