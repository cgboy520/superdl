/** Legal document page: h2 table of contents, anchors, version line and route switching. */
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

vi.mock("../../components/layout/AppTopBar", () => ({ AppTopBar: () => null }));
vi.mock("../../components/layout/SiteFooter", () => ({ SiteFooter: () => null }));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateSpy,
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

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
    title: "用户协议", // cjk-ok
    content_md: CONTENT,
    version: 3,
    published_at: "2026-08-01T02:00:00Z",
    fallback: false,
    ...over,
  };
}

describe("parseTocHeadings", () => {
  it("takes second-level headings only: skips h1 / h3 and ## inside fenced code blocks", () => {
    expect(parseTocHeadings(CONTENT)).toEqual([
      { id: "legal-h2-0", text: "First section" },
      { id: "legal-h2-1", text: "Second section" },
    ]);
  });

  it("empty without second-level headings (the page renders no table of contents)", () => {
    expect(parseTocHeadings("# Only title\n\nplain text")).toEqual([]);
  });
});

describe("legal document page", () => {
  beforeEach(() => {
    breakpoints.current = { lg: true };
    docState.current = { data: makeDoc(), isLoading: false, isError: false };
    navigateSpy.mockReset();
  });

  it("rendered h2 elements get the table-of-contents ids in order, anchors have targets", () => {
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    expect(document.querySelector("#legal-h2-0")?.textContent).toBe("First section");
    expect(document.querySelector("#legal-h2-1")?.textContent).toBe("Second section");
    expect(screen.getByRole("link", { name: "Second section" })).toHaveAttribute("href", "#legal-h2-1");
  });

  it("the version line follows the title, not the page end", () => {
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    const title = screen.getByRole("heading", { name: "用户协议" }); // cjk-ok
    const version = screen.getByText(/版本 v3/); // cjk-ok
    expect(title.compareDocumentPosition(version) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const firstH2 = document.querySelector("#legal-h2-0");
    expect(version.compareDocumentPosition(firstH2 as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the top switcher navigates to the privacy policy through the router", async () => {
    const user = userEvent.setup();
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    await user.click(screen.getByText("《隐私政策》")); // cjk-ok
    expect(navigateSpy).toHaveBeenCalledWith({ to: "/legal/privacy" });
  });

  it("renders no table of contents without second-level headings", () => {
    docState.current = { data: makeDoc({ content_md: "plain text only" }), isLoading: false, isError: false };
    render(
      <App>
        <LegalDocPage docKey="terms" />
      </App>,
    );
    expect(screen.queryByRole("navigation", { name: "目录" })).not.toBeInTheDocument(); // cjk-ok
  });
});
