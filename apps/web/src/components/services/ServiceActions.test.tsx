/** 服务操作槽位、端点菜单与未运行状态门控测试。 */
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
/** 带剪贴板 spy 的 userEvent 实例。 */
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

const BTN_ENDPOINT = /端\s*点/;
const BTN_STOP = /停\s*止/;
const BTN_START = /启\s*动/;

function renderWithApp(ui: ReactElement) {
  return render(<App>{ui}</App>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ServiceActions", () => {
  it("running 服务:主动作是「端点 ▾」,停止降为次动作(行里仍可停)", () => {
    renderWithApp(<ServiceActions service={makeService("running")} />);
    expect(screen.getByRole("button", { name: BTN_ENDPOINT })).toBeEnabled();
    expect(screen.getByRole("button", { name: BTN_STOP })).toBeEnabled();
    expect(screen.queryByRole("button", { name: BTN_START })).toBeNull();
  });

  it("stopped 服务:主动作回到「启动」,不给端点菜单(端点本来就打不通)", () => {
    renderWithApp(<ServiceActions service={makeService("stopped")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
    expect(screen.queryByRole("button", { name: BTN_ENDPOINT })).toBeNull();
  });

  it("端点菜单三条:复制访问地址写完整 URL,调用示例出 curl 弹窗", async () => {
    const user = userEvent.setup();
    stubClipboard();
    renderWithApp(<ServiceActions service={makeService("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_ENDPOINT }));
    expect(await screen.findByRole("menuitem", { name: "打开端点" })).toBeEnabled();
    await user.click(screen.getByRole("menuitem", { name: "复制访问地址" }));
    expect(writeText).toHaveBeenCalledWith("https://qwen-chat.svc.superdl.local");

    await user.click(screen.getByRole("button", { name: BTN_ENDPOINT }));
    await user.click(await screen.findByRole("menuitem", { name: "调用示例" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/curl https:\/\/qwen-chat\.svc\.superdl\.local/)).toBeInTheDocument();
  });

  it("frozen 服务:端点条目灰置带原因,点击不复制;启动降为次动作且门控", async () => {
    const user = userEvent.setup();
    stubClipboard();
    renderWithApp(<ServiceActions service={makeService("frozen")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByRole("button", { name: BTN_ENDPOINT }));
    const copy = await screen.findByRole("menuitem", { name: "复制访问地址" });
    expect(copy).toHaveAttribute("aria-disabled", "true");
    await user.click(copy);
    expect(writeText).not.toHaveBeenCalled();
  });
});
