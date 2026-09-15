/** 帮助中心的搜索、空态与按登录态分流的提单入口测试。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Route } from "./help";

const HelpPage = (Route as unknown as { component: ComponentType }).component;

const LAZY = { timeout: 10_000 };

const { loggedIn } = vi.hoisted(() => ({ loggedIn: { current: false } }));

vi.mock("../stores/auth", () => ({ useIsLoggedIn: () => loggedIn.current }));

vi.mock("../components/layout/AppTopBar", () => ({ AppTopBar: () => null }));
vi.mock("../components/layout/SiteFooter", () => ({ SiteFooter: () => null }));
vi.mock("../components/ContactCard", () => ({ ContactCard: () => null }));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: unknown) => opts,
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
});
