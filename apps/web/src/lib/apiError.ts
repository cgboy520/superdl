/** 应用侧错误文案 hook:typed-t 收窄为 LooseT 的唯一 cast 点(server key 天然动态)。 */
import { apiErrorText, type LooseT } from "@superdl/ui";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

export function useApiErrorText() {
  const { t } = useTranslation(["web", "errors"]);
  return useCallback(
    (err: unknown, fallback?: string) =>
      apiErrorText(t as unknown as LooseT, err, fallback ?? t("common.requestFailed")),
    [t],
  );
}
