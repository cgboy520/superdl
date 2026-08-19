import { adminThemeToken } from "@superdl/ui";
import { createRootRoute, Link, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, Button, ConfigProvider, Result, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { ReactNode } from "react";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
});

function DarkShell({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{ algorithm: theme.darkAlgorithm, token: adminThemeToken }}
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

/** 全局错误边界:渲染异常兜底,不白屏。 */
function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  return (
    <DarkShell>
      <Result
        status="500"
        title="页面出错了"
        subTitle={error instanceof Error ? error.message : "发生未知错误"}
        extra={
          <>
            <Button type="primary" onClick={() => reset()}>
              重 试
            </Button>
            <Button onClick={() => (window.location.href = "/")}>回总览</Button>
          </>
        }
      />
    </DarkShell>
  );
}

function NotFoundPage() {
  return (
    <DarkShell>
      <Result
        status="404"
        title="页面不存在"
        extra={
          <Link to="/">
            <Button type="primary">回总览</Button>
          </Link>
        }
      />
    </DarkShell>
  );
}
