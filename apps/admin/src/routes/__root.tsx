import { adminThemeComponents, adminThemeToken, CurrencyProvider, ThemeProvider } from "@superdl/ui";
import { NotFoundView, RouteErrorFallbackView } from "@superdl/ui/components";
import { createRootRoute, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider, theme } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAppLocale } from "@superdl/ui";
import { usePlatformConfig } from "../api";
import { useAuth } from "../stores/auth";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

/** Theme + locale + deployment currency (platform-config `deployment` block, only once signed in). */
function DarkShell({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  const { accessToken } = useAuth();
  const { data: platform } = usePlatformConfig({ enabled: accessToken != null });
  return (
    <ConfigProvider
      locale={antdLocale}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: adminThemeToken,
        components: adminThemeComponents,
      }}
    >
      <ThemeProvider value="admin">
        <CurrencyProvider currency={platform?.deployment.currency ?? null}>{children}</CurrencyProvider>
      </ThemeProvider>
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

/** Global error boundary (RouteErrorFallbackView shared with web). */
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
