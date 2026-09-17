/** Help centre: search, empty state and the sign-in-aware ticket entry. */
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

describe("help centre", () => {
  beforeEach(() => {
    loggedIn.current = false;
  });

  it("search filters by question + answer substring; clearing restores everything", async () => {
    const user = userEvent.setup();
    renderHelp();
    expect(await screen.findByText("JupyterLab 打不开怎么办?", undefined, LAZY)).toBeInTheDocument(); // cjk-ok

    await user.type(screen.getByLabelText("搜索帮助"), "jupyter"); // cjk-ok
    expect(screen.getByText("JupyterLab 打不开怎么办?")).toBeInTheDocument(); // cjk-ok
    expect(screen.queryByText("数据盘怎么算钱?")).not.toBeInTheDocument(); // cjk-ok
    expect(screen.queryByRole("link", { name: "计费" })).not.toBeInTheDocument(); // cjk-ok

    await user.clear(screen.getByLabelText("搜索帮助")); // cjk-ok
    expect(screen.getByText("数据盘怎么算钱?")).toBeInTheDocument(); // cjk-ok
  });

  it("no match shows the empty state with a clear-search exit", async () => {
    const user = userEvent.setup();
    renderHelp();
    await user.type(await screen.findByLabelText("搜索帮助", undefined, LAZY), "zzzz"); // cjk-ok
    expect(screen.getByText("没有匹配的问题,换个关键词试试")).toBeInTheDocument(); // cjk-ok

    await user.click(screen.getByRole("button", { name: "清除搜索" })); // cjk-ok
    expect(screen.getByText("怎么用 SSH 连接实例?")).toBeInTheDocument(); // cjk-ok
  });

  it("the signed-out not-solved link carries a /support return", async () => {
    renderHelp();
    const link = (await screen.findAllByRole("link", { name: "没解决?提交工单" }, LAZY))[0]; // cjk-ok
    expect(link).toHaveAttribute("href", "/login?redirect=/support");
  });

  it("the signed-in not-solved link goes straight to the ticket page", async () => {
    loggedIn.current = true;
    renderHelp();
    const link = (await screen.findAllByRole("link", { name: "没解决?提交工单" }, LAZY))[0]; // cjk-ok
    expect(link).toHaveAttribute("href", "/support");
  });
});
