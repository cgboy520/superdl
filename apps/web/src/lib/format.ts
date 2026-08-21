/** 把 makeFormatters 绑定到当前语言:调用点保持 formatMoney(x) 原形,只换来源为本 hook。 */
import { makeFormatters, type SharedT } from "@superdl/ui";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

export function useFormat() {
  const { t, i18n } = useTranslation(["web", "shared"]);
  const locale = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  // i18next v26 对插值参数做 per-key 严格 typing,宽松签名的 SharedT 无法直接承接;
  // 此处为约定的唯一 cast 点:key 存在性由 ui 的 FORMAT_KEYS 守护测试 + 双语真渲染测试锁定。
  return useMemo(() => makeFormatters(t as unknown as SharedT, locale), [t, locale]);
}
