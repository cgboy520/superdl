/** Service action slots, endpoint menu and not-running gating. */
import type { ServiceOut } from "@superdl/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactElement, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ServiceActions } from "./ServiceActions";

const { startMutate, stopMutateAsync, deleteMutate } = vi.hoisted(() => ({
  startMutate: vi.fn(),
  stopMutateAsync: vi.fn().mockResolvedValue(undefined),
  deleteMutate: vi.fn(),
}));

vi.mock("../../api/mutations", () => ({
  useStartService: () => ({ mutate: startMutate, isPending: false }),
  useStopService: () => ({ mutateAsync: stopMutateAsync, isPending: false }),
  useDeleteService: () => ({ mutate: deleteMutate, isPending: false }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
  useNavigate: () => vi.fn(),
}));

const writeText = vi.fn().mockResolvedValue(undefined);
/** userEvent instance with a clipboard spy. */
function stubClipboard(): void {
  Object.defineProperty(navigator, "clipboard", { configurable: true, writable: true, value: { writeText } });
}

function makeService(status: string, extra?: Partial<ServiceOut>): ServiceOut {
  return {
    slug: "qwen-chat",
    name: "qwen-chat",
    status,
    url: "https://qwen-chat.svc.superdl.local",
    revision: 3,
    require_api_key: true,
    ready: status === "running",
    ...extra,
  } as ServiceOut;
}

const BTN_ENDPOINT = /端\s*点/; // cjk-ok
const BTN_STOP = /停\s*止/; // cjk-ok
const BTN_START = /启\s*动/; // cjk-ok

function renderWithApp(ui: ReactElement) {
  return render(<App>{ui}</App>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ServiceActions", () => {
  it("running service: the primary action is Endpoint ▾, stop drops to secondary (still stoppable in the row)", () => {
    renderWithApp(<ServiceActions service={makeService("running")} />);
    expect(screen.getByRole("button", { name: BTN_ENDPOINT })).toBeEnabled();
    expect(screen.getByRole("button", { name: BTN_STOP })).toBeEnabled();
    expect(screen.queryByRole("button", { name: BTN_START })).toBeNull();
  });

  it("stopped service: the primary action is back to Start, no endpoint menu (the endpoint is unreachable anyway)", () => {
    renderWithApp(<ServiceActions service={makeService("stopped")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
    expect(screen.queryByRole("button", { name: BTN_ENDPOINT })).toBeNull();
  });

  it("three endpoint menu items: copy access URL writes the full URL, call example opens the curl dialog", async () => {
    const user = userEvent.setup();
    stubClipboard();
    renderWithApp(<ServiceActions service={makeService("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_ENDPOINT }));
    expect(await screen.findByRole("menuitem", { name: "打开端点" })).toBeEnabled(); // cjk-ok
    await user.click(screen.getByRole("menuitem", { name: "复制访问地址" })); // cjk-ok
    expect(writeText).toHaveBeenCalledWith("https://qwen-chat.svc.superdl.local");

    await user.click(screen.getByRole("button", { name: BTN_ENDPOINT }));
    await user.click(await screen.findByRole("menuitem", { name: "调用示例" })); // cjk-ok
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/curl https:\/\/qwen-chat\.svc\.superdl\.local/)).toBeInTheDocument();
  });

  it("frozen service: endpoint items greyed with a reason, click does not copy; start drops to secondary and is gated", async () => {
    const user = userEvent.setup();
    stubClipboard();
    renderWithApp(<ServiceActions service={makeService("frozen")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByRole("button", { name: BTN_ENDPOINT }));
    const copy = await screen.findByRole("menuitem", { name: "复制访问地址" }); // cjk-ok
    expect(copy).toHaveAttribute("aria-disabled", "true");
    await user.click(copy);
    expect(writeText).not.toHaveBeenCalled();
  });
});
