import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { apiErrorText, type LooseT } from "../apiError";

/** Returns a function that turns an API error into copy in the current language, with an optional fallback. */
export function useApiErrorText() {
  const { t } = useTranslation("shared");
  return useCallback(
    (err: unknown, fallback?: string) =>
      apiErrorText(t as unknown as LooseT, err, fallback ?? t("common.requestFailed")),
    [t],
  );
}
