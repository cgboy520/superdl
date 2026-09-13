/** 空值占位(两端统一):查询未就绪 / 字段为空一律显示「—」,不显假 0、不混用「-」。 */

import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export const EMPTY_VALUE = "—";

export function EmptyValue() {
  const { t } = useTranslation("shared");
  return <span aria-label={t("common.notAvailable")}>{EMPTY_VALUE}</span>;
}

/** 有值渲染 render(v),空值渲染 EmptyValue。 */
export function valueOr<T>(v: T | null | undefined, render: (v: T) => ReactNode): ReactNode {
  return v == null ? <EmptyValue /> : render(v);
}
