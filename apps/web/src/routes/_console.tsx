/** 控制台布局:全宽品牌顶栏(56px)压可折叠侧栏(200px,lg 断点收起);market 未登录可看。 */

import { fontSize, layout, space } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";
import { createFileRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Badge, Grid, Layout, Menu, theme, Typography } from "antd";

import { useUnreadCount } from "../api/queries";
import { CommandPalette } from "../components/CommandPalette";
import { AppTopBar } from "../components/layout/AppTopBar";
import { CONSOLE_NAV, consoleNavSelected } from "../components/layout/consoleNav";
import { TopBarUser } from "../components/layout/TopBarUser";
import { useGlobalHotkeys } from "../lib/useGlobalHotkeys";
import { useThemeMode } from "../stores/theme";

export const Route = createFileRoute("/_console")({
  component: ConsoleLayout,
});

function ConsoleLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const mode = useThemeMode();
  const screens = Grid.useBreakpoint();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const selected = consoleNavSelected(pathname);
  // 全局快捷键:/ 聚焦页内搜索框,g+序列导航(d/i/b/m)
  useGlobalHotkeys();
  // 侧栏「通知」未读角标与顶栏铃铛同一轻端点(react-query 30s 轮询共享缓存,不增发请求)
  const { data: unread } = useUnreadCount({ refetchInterval: 30_000 });
  const unreadCount = unread?.unread_count ?? 0;

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
            items={CONSOLE_NAV.map((n) => ({
              key: n.key,
              icon: n.icon,
              label:
                n.key === "/notifications" && unreadCount > 0 ? (
                  <Badge count={unreadCount} size="small" offset={[6, 0]}>
                    {t(n.labelKey)}
                  </Badge>
                ) : (
                  t(n.labelKey)
                ),
            }))}
            onClick={({ key }) => void navigate({ to: key })}
            style={{ borderInlineEnd: "none", paddingTop: 8 }}
          />
        </Layout.Sider>
        <Layout>
          {/* 内容区 padding 随断点:<md 收紧为 16(space.lg) */}
          <Layout.Content style={{ padding: screens.md ? layout.contentPadding : space.lg }}>
            <PageContainer>
              <Outlet />
            </PageContainer>
          </Layout.Content>
          <Layout.Footer style={{ textAlign: "center", paddingBlock: 16 }}>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("copy.antiMiningNotice")}
            </Typography.Text>
          </Layout.Footer>
        </Layout>
      </Layout>
      <CommandPalette />
    </div>
  );
}
