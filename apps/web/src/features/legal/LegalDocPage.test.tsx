/** 法务文档页:目录取二级标题(跳过代码块与三级标题)、渲染后的 h2 补上对应 id、版本行紧跟标题、Segmented 切文档走路由。
 *  挂了说明:右侧目录锚点点了不动(id 没落到 h2 上)、目录把代码块里的 ## 当标题、版本行又漂回页尾、切换器不换文档。 */
import type { LegalDocOut } from "@superdl/api-client";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LegalDocPage, parseTocHeadings } from "./LegalDocPage";

const { docState, breakpoints, navigateSpy } = vi.hoisted(() => {
  const docState = { current: { data: undefined as LegalDocOut | undefined, isLoading: false, isError: false } };
  const breakpoints: { current: Record<string, boolean> } = { current: {} };
  return { docState, breakpoints, navigateSpy: vi.fn() };
});

vi.mock("../../api/queries", () => ({
  useLegalDoc: () => ({ ...docState.current, refetch: vi.fn() }),
}));

// 顶栏 / 页脚另有归属,本用例只看正文与目录
vi.mock("../../components/layout/AppTopBar", () => ({ AppTopBar: () => null }));
vi.mock("../../components/layout/SiteFooter", () => ({ SiteFooter: () => null }));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateSpy,
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

// 断点由用例控制:目录只在 lg 以上渲染
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return { ...antd, Grid: { ...antd.Grid, useBreakpoint: () => breakpoints.current } };
});

const CONTENT = [
  "# Title",
  "",
  "## First section",
  "text",
  "### Nested heading",
  "```",
  "## not a heading",
  "```",
  "## Second section",
  "more text",
].join("\n");

function makeDoc(over: Partial<LegalDocOut> = {}): LegalDocOut {
  return {
    doc_key: "terms",
    locale: "zh-CN",
    title: "用户协议",
    content_md: CONTENT,
    version: 3,
    published_at: "2026-08-01T02:00:00Z",
    fallback: false,
    ...over,
  };
}

describe("parseTocHeadings", () => {
  it("只取二级标题:跳过一级/三级标题与围栏代码块里的 ##", () => {
    expect(parseTocHeadings(CONTENT)).toEqual([
      { id: "legal-h2-0", text: "First section" },
      { id: "legal-h2-1", text: "Second section" },
    ]);
  });

  it("无二级标题时为空(页面据此不渲染目录)", () => {
    expect(parseTocHeadings("# Only title\n\nplain text")).toEqual([]);
  });
});

describe("法务文档页", () => {
  beforeEach(() => {
    breakpoints.current = { lg: true };
    docState.current = { data: makeDoc(), isLoading: false, isError: false };
    navigateSpy.mockReset();
  });

  it("渲染后的 h2 按顺序拿到目录 id,锚点有落点", () => {
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    expect(document.querySelector("#legal-h2-0")?.textContent).toBe("First section");
    expect(document.querySelector("#legal-h2-1")?.textContent).toBe("Second section");
    expect(screen.getByRole("link", { name: "Second section" })).toHaveAttribute("href", "#legal-h2-1");
  });

  it("版本行紧跟标题,不落到页尾", () => {
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    const title = screen.getByRole("heading", { name: "用户协议" });
    const version = screen.getByText(/版本 v3/);
    // 标题在版本行之前,且版本行在正文首个二级标题之前
    expect(title.compareDocumentPosition(version) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const firstH2 = document.querySelector("#legal-h2-0");
    expect(version.compareDocumentPosition(firstH2 as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("顶部切换器切到隐私政策走路由跳转", async () => {
    const user = userEvent.setup();
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    await user.click(screen.getByText("《隐私政策》"));
    expect(navigateSpy).toHaveBeenCalledWith({ to: "/legal/privacy" });
  });

  it("无二级标题时不渲染目录", () => {
    docState.current = { data: makeDoc({ content_md: "plain text only" }), isLoading: false, isError: false };
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    expect(screen.queryByRole("navigation", { name: "目录" })).not.toBeInTheDocument();
  });
});
