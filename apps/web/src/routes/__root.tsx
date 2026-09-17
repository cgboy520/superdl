import { brand, cssVars, CurrencyProvider, ThemeProvider, webDarkColors, webDarkTheme, webTheme } from "@superdl/ui";
import { NotFoundView, RouteErrorFallbackView } from "@superdl/ui/components";
import { createRootRoute, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider, theme as antdTheme } from "antd";
import { MotionConfig } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAppLocale } from "@superdl/ui";
import { useSiteConfig } from "../api/queries";
import { useThemeMode } from "../stores/theme";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

/** The single Provider linking locale + theme + deployment currency: shared by the main tree / error boundary / 404. Dark = darkAlgorithm + webDarkTheme overrides;
 *  the currency comes from /site-config, amounts show the number only until it is known. */
function AppProviders({ children }: { children: ReactNode }) {
  const antdLocale = useAppLocale();
  const mode = useThemeMode();
  const { data: site } = useSiteConfig();
  useEffect(() => {
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
      theme={mode === "dark" ? { algorithm: antdTheme.darkAlgorithm, ...webDarkTheme } : webTheme}
    >
      <ThemeProvider value={mode === "dark" ? "web-dark" : "web-light"}>
        <CurrencyProvider currency={site?.currency ?? null}>
          <MotionConfig reducedMotion="user">
            <AntApp>{children}</AntApp>
          </MotionConfig>
        </CurrencyProvider>
      </ThemeProvider>
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

/** Global error boundary: render exceptions fall back to a recoverable page (the Result body shares RouteErrorFallbackView with admin). */
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
        homeTo="/instances"
        homeLabel={t("common.backConsole")}
        subtitle={t("notFound.subtitle", { ns: "shared" })}
      />
    </AppProviders>
  );
}
