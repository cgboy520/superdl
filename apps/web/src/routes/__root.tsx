import { webTheme } from "@superdl/ui";
import { createRootRoute, Link, Outlet, type ErrorComponentProps } from "@tanstack/react-router";
import { App as AntApp, Button, ConfigProvider, Result } from "antd";
import zhCN from "antd/locale/zh_CN";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: RouteErrorFallback,
  notFoundComponent: NotFoundPage,
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

/** 全局错误边界:任何渲染异常兜底为可恢复页面,不再白屏。 */
function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  return (
    <ConfigProvider locale={zhCN} theme={webTheme}>
      <Result
        status="500"
        title="页面出错了"
        subTitle={error instanceof Error ? error.message : "发生未知错误,请重试或返回首页"}
        extra={
          <>
            <Button type="primary" onClick={() => reset()}>
              重 试
            </Button>
            <Button onClick={() => (window.location.href = "/")}>回首页</Button>
          </>
        }
      />
    </ConfigProvider>
  );
}

function NotFoundPage() {
  return (
    <ConfigProvider locale={zhCN} theme={webTheme}>
      <Result
        status="404"
        title="页面不存在"
        subTitle="你访问的地址不存在或已被移除"
        extra={
          <Link to="/dashboard">
            <Button type="primary">回控制台</Button>
          </Link>
        }
      />
    </ConfigProvider>
  );
}
