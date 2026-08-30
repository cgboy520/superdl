/** 错误文案 hook(两端共用):typed-t 收窄为 LooseT 的唯一 cast 点(server key 天然动态)。 */
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { apiErrorText, type LooseT } from "../apiError";

export function useApiErrorText() {
  const { t } = useTranslation("shared");
  return useCallback(
    (err: unknown, fallback?: string) =>
      apiErrorText(t as unknown as LooseT, err, fallback ?? t("common.requestFailed")),
    [t],
  );
}
