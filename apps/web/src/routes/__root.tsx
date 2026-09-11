import { brand, cssVars, webDarkColors, webDarkTheme, webTheme } from "@superdl/ui";
import { NotFoundView, RouteErrorFallbackView } from "@superdl/ui/components";
import { createRootRoute, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider, theme as antdTheme } from "antd";
import { MotionConfig } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAppLocale } from "@superdl/ui";
import { useThemeMode } from "../stores/theme";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

/** locale + 主题联动的唯一 Provider:主树/错误边界/404 共用。暗色 = darkAlgorithm + webDarkTheme 覆写。 */
function AppProviders({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  const mode = useThemeMode();
  useEffect(() => {
    // CSS 变量面(抽屉链接/滚动条/focus 描边/命令面板选中底)与 color-scheme 随主题
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode;
    document.body.style.background = mode === "dark" ? webDarkColors.bgBase : brand.pageBg;
    const vars = mode === "dark" ? cssVars.dark : cssVars.light;
    for (const [k, v] of Object.entries(vars)) {
      document.documentElement.style.setProperty(k, v);
    }
  }, [mode]);
  return (
    <ConfigProvider
      locale={antdLocale}
      theme={
        mode === "dark" ? { algorithm: antdTheme.darkAlgorithm, ...webDarkTheme } : webTheme
      }
    >
      {/* 跟随系统「减弱动态效果」 */}
      <MotionConfig reducedMotion="user">
        <AntApp>{children}</AntApp>
      </MotionConfig>
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

/** 全局错误边界:渲染异常兜底为可恢复页面(Result 体与 admin 共用 RouteErrorFallbackView)。 */
function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  return (
    <AppProviders>
      <RouteErrorFallbackView error={error} reset={reset} homeLabel={t("common.backHome")} />
    </AppProviders>
  );
}

function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <AppProviders>
      <NotFoundView
        homeTo="/dashboard"
        homeLabel={t("common.backConsole")}
        // 404 文案在 packages/ui shared ns(errorPage/notFound)
        subtitle={t("notFound.subtitle", { ns: "shared" })}
      />
    </AppProviders>
  );
}
