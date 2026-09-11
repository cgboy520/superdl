import { adminThemeComponents, adminThemeToken } from "@superdl/ui";
import { NotFoundView, RouteErrorFallbackView } from "@superdl/ui/components";
import { createRootRoute, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider, theme } from "antd";
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

/** 全局错误边界(与 web 共用 RouteErrorFallbackView)。 */
function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  return (
    <DarkShell>
      <RouteErrorFallbackView error={error} reset={reset} homeLabel={t("common.backOverview")} />
    </DarkShell>
  );
}

function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <DarkShell>
      <NotFoundView homeTo="/" homeLabel={t("common.backOverview")} />
    </DarkShell>
  );
}
