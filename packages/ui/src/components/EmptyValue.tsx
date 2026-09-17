/** Empty value placeholder (shared by both consoles): pending queries / empty fields always show "—", never a fake 0 or a mixed "-". */

import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export const EMPTY_VALUE = "—";

export function EmptyValue() {
  const { t } = useTranslation("shared");
  return <span aria-label={t("common.notAvailable")}>{EMPTY_VALUE}</span>;
}

/** Render render(v) when there is a value, EmptyValue otherwise. */
export function valueOr<T>(v: T | null | undefined, render: (v: T) => ReactNode): ReactNode {
  return v == null ? <EmptyValue /> : render(v);
}
