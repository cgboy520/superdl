import { webTheme } from "@superdl/ui";
import { createRootRoute, Outlet } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <ConfigProvider locale={zhCN} theme={webTheme}>
      <AntApp>
        <Outlet />
      </AntApp>
    </ConfigProvider>
  );
}
