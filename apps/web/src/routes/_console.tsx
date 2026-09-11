/** 控制台布局:中性色顶栏(56px)压可折叠侧栏(200px,lg 断点收起;窄屏导航走顶栏汉堡 Drawer);market 未登录可看。
 *  <main id="main"> 地标 + 跳转链接给键盘用户;合规声明只在公开页脚与市场页脚,控制台不再常驻。 */

import { layout, space } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";
import { createFileRoute, Outlet } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Grid, Layout, theme } from "antd";

import { CommandPalette } from "../components/CommandPalette";
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
        {/* 窄屏不渲染 Sider(导航走 Drawer),避免两套并行 */}
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
          {/* antd Layout.Content 即 <main>;id 供 skip-link 定位 */}
          <Layout.Content
            id="main"
            tabIndex={-1}
            style={{ padding: screens.md ? layout.contentPadding : space.lg, outline: "none" }}
          >
            <PageContainer>
              <Outlet />
            </PageContainer>
          </Layout.Content>
        </Layout>
      </Layout>
      <CommandPalette />
    </div>
  );
}
