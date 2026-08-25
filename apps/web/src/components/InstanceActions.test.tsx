/**
 * InstanceActions / ReleaseModal 组件测试:状态驱动的禁用态、关机确认弹窗、
 * 释放多级防护(键入实例名才解锁)。写操作 hooks 全部 mock 掉,不走网络。
 */
import type { InstanceOut } from "@superdl/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InstanceActions, ReleaseModal } from "./InstanceActions";

const { startMutate, stopMutateAsync, restartMutateAsync, releaseMutate } = vi.hoisted(() => ({
  startMutate: vi.fn(),
  stopMutateAsync: vi.fn().mockResolvedValue(undefined),
  restartMutateAsync: vi.fn().mockResolvedValue(undefined),
  releaseMutate: vi.fn(),
}));

vi.mock("../api/mutations", () => ({
  useStartInstance: () => ({ mutate: startMutate, isPending: false }),
  useStopInstance: () => ({ mutateAsync: stopMutateAsync, isPending: false }),
  useRestartInstance: () => ({ mutateAsync: restartMutateAsync, isPending: false }),
  useReleaseInstance: () => ({ mutate: releaseMutate, isPending: false }),
}));

function makeInstance(status: string): InstanceOut {
  return { uuid: "u-1", name: "demo-vm", status } as InstanceOut;
}

function renderWithApp(ui: React.ReactElement) {
  return render(<App>{ui}</App>);
}

// hoisted mock 的调用历史跨用例保留,逐用例清零防串扰
beforeEach(() => {
  vi.clearAllMocks();
});

// antd Button 对两字中文自动插空(autoInsertSpace),可访问名是「开 机」而非「开机」
const BTN_START = /开\s*机/;
const BTN_STOP = /关\s*机/;
const BTN_MORE = /更\s*多/;

describe("InstanceActions", () => {
  it("stopped 实例:开机可用,关机禁用(灰置而非隐藏)", () => {
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
    expect(screen.getByRole("button", { name: BTN_STOP })).toBeDisabled();
  });

  it("running 实例点关机:弹确认框,确认后触发 stop", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_STOP }));
    const dialog = await screen.findByRole("dialog");
    // antd v6 confirm 标题渲染两处(.ant-modal-title 与 .ant-modal-confirm-title)
    expect(within(dialog).getAllByText("确认关机?").length).toBeGreaterThan(0);
    await user.click(within(dialog).getByRole("button", { name: BTN_STOP }));
    expect(stopMutateAsync).toHaveBeenCalledWith("u-1");
  });

  it("frozen 实例:开机禁用(欠费冻结前置条件)", () => {
    renderWithApp(<InstanceActions instance={makeInstance("frozen")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeDisabled();
    expect(screen.getByRole("button", { name: BTN_STOP })).toBeDisabled();
  });

  it("释放全链路:更多 → 释放实例 → 键入名称 + 勾选清盘才解锁 → 触发 release", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByText("释放实例"));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认释放" });
    expect(confirm).toBeDisabled();
    // 两道闸缺一不可(ui-ux-spec 规则 4):只键入名字仍锁着
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(releaseMutate).toHaveBeenCalledWith("u-1");
  });
});

describe("ReleaseModal", () => {
  it("键入名不匹配时确认按钮保持禁用", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReleaseModal instance={makeInstance("stopped")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认释放" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm-typo");
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeDisabled();
    expect(releaseMutate).not.toHaveBeenCalled();
  });

  it("creating 取消创建:尚未落盘,不弹清盘勾选,键入名字即解锁", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReleaseModal instance={makeInstance("creating")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    const confirm = within(dialog).getByRole("button", { name: "确认取消" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirm).toBeEnabled();
  });
});
