/** 控制台布局:全宽品牌顶栏(56px)压可折叠侧栏(200px,lg 断点收起);market 未登录可看。 */

import { createFileRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Layout, Menu, theme, Typography } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { CONSOLE_NAV, consoleNavSelected } from "../components/layout/consoleNav";
import { TopBarUser } from "../components/layout/TopBarUser";
import { useThemeMode } from "../stores/theme";

export const Route = createFileRoute("/_console")({
  component: ConsoleLayout,
});

function ConsoleLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const mode = useThemeMode();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const selected = consoleNavSelected(pathname);

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="console" right={<TopBarUser />} />
      <Layout style={{ flex: 1 }}>
        <Layout.Sider
          width={200}
          theme={mode === "dark" ? "dark" : "light"}
          collapsible
          breakpoint="lg"
          collapsedWidth={0}
          style={{ borderRight: `1px solid ${token.colorBorderSecondary}` }}
        >
          <Menu
            mode="inline"
            theme={mode === "dark" ? "dark" : "light"}
            selectedKeys={[selected]}
            items={CONSOLE_NAV.map((n) => ({ key: n.key, icon: n.icon, label: t(n.labelKey) }))}
            onClick={({ key }) => void navigate({ to: key })}
            style={{ borderInlineEnd: "none", paddingTop: 8 }}
          />
        </Layout.Sider>
        <Layout>
          <Layout.Content style={{ padding: 24 }}>
            <div style={{ maxWidth: 1280, margin: "0 auto" }}>
              <Outlet />
            </div>
          </Layout.Content>
          <Layout.Footer style={{ textAlign: "center", paddingBlock: 16 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("copy.antiMiningNotice")}
            </Typography.Text>
          </Layout.Footer>
        </Layout>
      </Layout>
    </div>
  );
}
