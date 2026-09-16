/** Route-level error pages (shared by both __root.tsx files): global error boundary + 404. Copy lives in the shared ns; each console brings its Provider shell; error.message is shown collapsed. */

import { Button, Result } from "antd";
import { useTranslation } from "react-i18next";

export function RouteErrorFallbackView({
  error,
  reset,
  homeLabel,
}: {
  error: unknown;
  reset: () => void;
  /** Home button copy */
  homeLabel: string;
}) {
  const { t } = useTranslation("shared");
  const detail = error instanceof Error ? error.message : null;
  return (
    <Result
      status="500"
      title={t("errorPage.title")}
      subTitle={
        <>
          {t("errorPage.subtitle")}
          {detail ? (
            <details style={{ marginTop: 8, fontSize: 12, opacity: 0.65 }}>
              <summary>{t("errorPage.techDetail")}</summary>
              <code>{detail}</code>
            </details>
          ) : null}
        </>
      }
      extra={
        <>
          <Button type="primary" onClick={() => reset()}>
            {t("common.retry")}
          </Button>
          <Button onClick={() => (window.location.href = "/")}>{homeLabel}</Button>
        </>
      }
    />
  );
}

export function NotFoundView({
  homeTo,
  homeLabel,
  subtitle,
}: {
  /** Return target */
  homeTo: string;
  homeLabel: string;
  /** Subtitle (optional) */
  subtitle?: string;
}) {
  const { t } = useTranslation("shared");
  return (
    <Result
      status="404"
      title={t("notFound.title")}
      subTitle={subtitle}
      extra={
        <Button type="primary" href={homeTo}>
          {homeLabel}
        </Button>
      }
    />
  );
}
