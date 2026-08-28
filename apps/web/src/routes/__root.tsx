import { webDarkColors, webDarkTheme, webTheme } from "@superdl/ui";
import { createRootRoute, Link, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, Button, ConfigProvider, Result, theme as antdTheme } from "antd";
import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAppLocale } from "../lib/locale";
import { useThemeMode } from "../stores/theme";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

/** locale + 主题联动的唯一 Provider:主树/错误边界/404 共用(后两者渲染在主树之外,须自带)。
 *  暗色 = darkAlgorithm + webDarkTheme 覆写(浅色系 token 与组件覆盖全部继承)。 */
function AppProviders({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  const mode = useThemeMode();
  useEffect(() => {
    // CSS 变量面(抽屉链接/滚动条等非 token 覆盖区)与 color-scheme 随动
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode;
    document.body.style.background = mode === "dark" ? webDarkColors.bgBase : "#F5F6FA";
  }, [mode]);
  return (
    <ConfigProvider
      locale={antdLocale}
      theme={
        mode === "dark" ? { algorithm: antdTheme.darkAlgorithm, ...webDarkTheme } : webTheme
      }
    >
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
