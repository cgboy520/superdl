import { adminThemeComponents, adminThemeToken } from "@superdl/ui";
import { createRootRoute, Link, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, Button, ConfigProvider, Result, theme } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAppLocale } from "@superdl/ui";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

function DarkShell({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  return (
    <ConfigProvider
      locale={antdLocale}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: adminThemeToken,
        components: adminThemeComponents,
      }}
    >
      {children}
    </ConfigProvider>
  );
}

function RootLayout() {
  return (
    <DarkShell>
      <AntApp>
        <Outlet />
      </AntApp>
    </DarkShell>
  );
}

/** 全局错误边界:渲染异常兜底,不白屏。
 *  不直出 error.message(技术文本对值班无意义),技术详情折叠供排查。 */
function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  const detail = error instanceof Error ? error.message : null;
  return (
    <DarkShell>
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
            <Button onClick={() => (window.location.href = "/")}>{t("common.backOverview")}</Button>
          </>
        }
      />
    </DarkShell>
  );
}

function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <DarkShell>
      <Result
        status="404"
        title={t("notFound.title")}
        extra={
          <Link to="/">
            <Button type="primary">{t("common.backOverview")}</Button>
          </Link>
        }
      />
    </DarkShell>
  );
}
