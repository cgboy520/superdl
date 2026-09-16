/** Query state rendering (shared by both consoles): moneyOr shows "—" for pending amounts; DataErrorAlert is the page-level "part of the data failed to load" banner. */

import { Alert, Button } from "antd";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

export function moneyOr(formatted: string, ready: boolean): string {
  return ready ? formatted : "—";
}

export function DataErrorAlert({
  onRetry,
  title,
  description,
  style,
}: {
  onRetry: () => void;
  /** Override the default copy */
  title?: string;
  /** null = no description row (single-line error bar) */
  description?: string | null;
  style?: CSSProperties;
}) {
  const { t } = useTranslation("shared");
  return (
    <Alert
      type="error"
      showIcon
      style={style}
      title={title ?? t("query.partialFailed")}
      description={description === null ? undefined : (description ?? t("query.partialFailedDesc"))}
      action={
        <Button size="small" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      }
    />
  );
}
