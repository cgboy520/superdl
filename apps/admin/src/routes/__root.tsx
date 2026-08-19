import { adminThemeToken } from "@superdl/ui";
import { createRootRoute, Outlet } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{ algorithm: theme.darkAlgorithm, token: adminThemeToken }}
    >
      <AntApp>
        <Outlet />
      </AntApp>
    </ConfigProvider>
  );
}
