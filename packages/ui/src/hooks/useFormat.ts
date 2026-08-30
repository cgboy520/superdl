/** 把 makeFormatters 绑定到当前语言(两端共用):调用点保持 formatMoney(x) 原形,只换来源为本 hook。 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { makeFormatters, type SharedT } from "../format";

export function useFormat() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  // typed-t 收窄 cast 点:i18next v26 对插值参数按 key 严格 typing,宽松签名的 SharedT 无法直接承接。
  return useMemo(() => makeFormatters(t as unknown as SharedT, locale), [t, locale]);
}
