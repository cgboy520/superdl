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

/** locale + 主题联动的唯一 Provider:主树/错误边界/404 共用(后两者渲染在主树之外,须自带)。
 *  暗色 = darkAlgorithm + webDarkTheme 覆写(浅色系 token 与组件覆盖全部继承)。 */
function AppProviders({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  const mode = useThemeMode();
  useEffect(() => {
    // CSS 变量面(抽屉链接/滚动条/focus 描边/命令面板选中底等非 token 覆盖区)与 color-scheme 随动
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
      {/* 全局动效策略:尊重系统减弱动态效果设置(transform/layout 动效自动禁用) */}
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

/** 全局错误边界:渲染异常兜底为可恢复页面,不白屏(Result 体与 admin 共用 RouteErrorFallbackView)。 */
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
        subtitle={t("notFound.subtitle")}
      />
    </AppProviders>
  );
}
