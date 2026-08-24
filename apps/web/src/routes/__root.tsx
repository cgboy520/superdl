import { webTheme } from "@superdl/ui";
import { createRootRoute, Link, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, Button, ConfigProvider, Result } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAppLocale } from "../lib/locale";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

/** locale 联动的唯一 Provider:主树/错误边界/404 共用(后两者渲染在主树之外,须自带)。 */
function AppProviders({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  return (
    <ConfigProvider locale={antdLocale} theme={webTheme}>
      <AntApp>{children}</AntApp>
    </ConfigProvider>
  );
}

function RootLayout() {
  return (
    <AppProviders>
      <Outlet />
    </AppProviders>
  );
}

/** 全局错误边界:渲染异常兜底为可恢复页面,不白屏。 */
function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  return (
    <AppProviders>
      <Result
        status="500"
        title={t("errorPage.title")}
        subTitle={error instanceof Error ? error.message : t("errorPage.unknown")}
        extra={
          <>
            <Button type="primary" onClick={() => reset()}>
              {t("common.retry")}
            </Button>
            <Button onClick={() => (window.location.href = "/")}>{t("common.backHome")}</Button>
          </>
        }
      />
    </AppProviders>
  );
}

function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <AppProviders>
      <Result
        status="404"
        title={t("notFound.title")}
        subTitle={t("notFound.subtitle")}
        extra={
          <Link to="/dashboard">
            <Button type="primary">{t("common.backConsole")}</Button>
          </Link>
        }
      />
    </AppProviders>
  );
}
