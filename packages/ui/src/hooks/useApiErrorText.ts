import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { apiErrorText, type LooseT } from "../apiError";

/** 返回将 API 错误转换为当前语言文案的函数,支持自定义兜底。 */
export function useApiErrorText() {
  const { t } = useTranslation("shared");
  return useCallback(
    (err: unknown, fallback?: string) =>
      apiErrorText(t as unknown as LooseT, err, fallback ?? t("common.requestFailed")),
    [t],
  );
}
