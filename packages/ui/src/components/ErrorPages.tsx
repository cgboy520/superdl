/** 路由级错误页(两端 __root.tsx 共用):全局错误边界 + 404。文案走 shared ns;Provider 外壳由各端自带;error.message 折叠展示。 */

import { Button, Result } from "antd";
import { useTranslation } from "react-i18next";

export function RouteErrorFallbackView({
  error,
  reset,
  homeLabel,
}: {
  error: unknown;
  reset: () => void;
  /** 回首页按钮文案 */
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
  /** 回跳目标 */
  homeTo: string;
  homeLabel: string;
  /** 副标题(可选) */
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
