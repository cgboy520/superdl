/** 帮助中心:搜索过滤与空态、「没解决?提交工单」按登录态分流。
 *  挂了说明:搜索框筛不掉无关问答(或筛没了不给清除出口)、未登录的提单入口没带回跳(登录后落不回 /support)。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Route } from "./help";

const HelpPage = (Route as unknown as { component: ComponentType }).component;

/** lazyRouteComponent 首次解析要过一次 vite transform,给足超时 */
const LAZY = { timeout: 10_000 };

const { loggedIn } = vi.hoisted(() => ({ loggedIn: { current: false } }));

vi.mock("../stores/auth", () => ({ useIsLoggedIn: () => loggedIn.current }));

// 顶栏 / 页脚 / 联系卡另有归属,本用例只看帮助中心主体
vi.mock("../components/layout/AppTopBar", () => ({ AppTopBar: () => null }));
vi.mock("../components/layout/SiteFooter", () => ({ SiteFooter: () => null }));
vi.mock("../components/ContactCard", () => ({ ContactCard: () => null }));

// 无 Router 上下文:Link 降级成原生 <a>,search.redirect 拼进 href 以便断言回跳
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: unknown) => opts,
  // autoCodeSplitting 后组件经 lazyRouteComponent 异步加载:测试里还原成 React.lazy
  lazyRouteComponent: (loader: () => Promise<Record<string, ComponentType>>, name: string) =>
    lazy(async () => ({ default: (await loader())[name] as ComponentType })),
  useRouterState: () => "",
  Link: ({ to, search, children }: { to: string; search?: { redirect?: string }; children: ReactNode }) => (
    <a href={search?.redirect ? `${to}?redirect=${search.redirect}` : to}>{children}</a>
  ),
}));

function renderHelp() {
  return render(
    <App>
      <Suspense fallback={null}>
        <HelpPage />
      </Suspense>
    </App>,
  );
}

describe("帮助中心", () => {
  beforeEach(() => {
    loggedIn.current = false;
  });

  it("搜索按问 + 答子串过滤;清除后恢复全部", async () => {
    const user = userEvent.setup();
    renderHelp();
    expect(await screen.findByText("JupyterLab 打不开怎么办?", undefined, LAZY)).toBeInTheDocument();

    await user.type(screen.getByLabelText("搜索帮助"), "jupyter");
    expect(screen.getByText("JupyterLab 打不开怎么办?")).toBeInTheDocument();
    expect(screen.queryByText("数据盘怎么算钱?")).not.toBeInTheDocument();
    // 分类锚点跟着收敛:只剩命中分类
    expect(screen.queryByRole("link", { name: "计费" })).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("搜索帮助"));
    expect(screen.getByText("数据盘怎么算钱?")).toBeInTheDocument();
  });

  it("搜不到时出空态,且给「清除搜索」出口", async () => {
    const user = userEvent.setup();
    renderHelp();
    await user.type(await screen.findByLabelText("搜索帮助", undefined, LAZY), "zzzz");
    expect(screen.getByText("没有匹配的问题,换个关键词试试")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(screen.getByText("怎么用 SSH 连接实例?")).toBeInTheDocument();
  });

  it("未登录的「没解决?提交工单」带 /support 回跳", async () => {
    renderHelp();
    expect((await screen.findAllByRole("link", { name: "没解决?提交工单" }, LAZY))[0]).toHaveAttribute(
      "href",
      "/login?redirect=/support",
    );
  });

  it("已登录的「没解决?提交工单」直达工单页", async () => {
    loggedIn.current = true;
    renderHelp();
    expect((await screen.findAllByRole("link", { name: "没解决?提交工单" }, LAZY))[0]).toHaveAttribute(
      "href",
      "/support",
    );
  });

  it("快速开始给出可复制的 SSH 命令样例", async () => {
    renderHelp();
    expect(await screen.findByText("ssh -p <端口> root@<主机>", undefined, LAZY)).toBeInTheDocument();
  });
});
