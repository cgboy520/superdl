/** Console layout: neutral top bar (56px) over a collapsible sidebar (200px, collapses at the lg breakpoint; narrow screens navigate through the top-bar hamburger Drawer); market is browsable when signed out.
 *  <main id="main"> landmark + skip link for keyboard users; the compliance statement lives only in the public and market footers, no longer permanent in the console. */

import { layout, space } from "@superdl/ui";
import { createFileRoute, Outlet } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Grid, Layout, theme } from "antd";

import { CommandPalette } from "../components/CommandPalette";
import { EmailClaimBanner } from "../components/layout/EmailClaimBanner";
import { AppTopBar } from "../components/layout/AppTopBar";
import { ConsoleNavMenu } from "../components/layout/ConsoleNavMenu";
import { TopBarUser } from "../components/layout/TopBarUser";
import { useGlobalHotkeys } from "../lib/useGlobalHotkeys";
import { useThemeMode } from "../stores/theme";

export const Route = createFileRoute("/_console")({
  component: ConsoleLayout,
});

function ConsoleLayout() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const mode = useThemeMode();
  const screens = Grid.useBreakpoint();
  useGlobalHotkeys();

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <a href="#main" className="skip-link">
        {t("common.skipToMain")}
      </a>
      <AppTopBar variant="console" right={<TopBarUser />} />
      <Layout style={{ flex: 1 }}>
        {screens.lg && (
          <Layout.Sider
            width={200}
            theme={mode === "dark" ? "dark" : "light"}
            style={{ borderRight: `1px solid ${token.colorBorderSecondary}` }}
          >
            <nav aria-label={t("nav.primary")}>
              <ConsoleNavMenu />
            </nav>
          </Layout.Sider>
        )}
        <Layout>
          <Layout.Content
            id="main"
            tabIndex={-1}
            style={{ padding: screens.md ? layout.contentPadding : space.lg, outline: "none" }}
          >
            <EmailClaimBanner />
            <Outlet />
          </Layout.Content>
        </Layout>
      </Layout>
      <CommandPalette />
    </div>
  );
}
