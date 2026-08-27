/**
 * ApiKeyModal 一次性展示的守护:明文 Key 只在创建响应里出现一次,库里只有 HMAC 摘要。
 * 这几条挂了 = 用户能在没保存 Key 的情况下把窗口关掉,而 Key 再也取不回来(只能吊销重建)。
 * 写操作 hook 全 mock,不走网络。
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiKeyModal } from "./ApiKeyModal";

const PLAINTEXT = "sk-a1b2c3d4e5f60718293a4b5c6d7e8f90";

const { createMutate, onSuccessRef } = vi.hoisted(() => ({
  createMutate: vi.fn(),
  onSuccessRef: { current: undefined as ((d: unknown) => void) | undefined },
}));

vi.mock("../api/mutations", () => ({
  useCreateApiKey: (_uuid: string, o?: { onSuccess?: (d: unknown) => void }) => {
    onSuccessRef.current = o?.onSuccess;
    return { mutate: createMutate, isPending: false };
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  onSuccessRef.current = undefined;
});

/** 走一遍「填名称 → 创建 → 服务端回明文」,停在成功态 */
async function openSuccessState(onClose = vi.fn()) {
  const user = userEvent.setup();
  render(
    <App>
      <ApiKeyModal uuid="u-1" open onClose={onClose} />
    </App>,
  );
  await user.type(screen.getByLabelText("名称"), "线上推理");
  await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
  expect(createMutate).toHaveBeenCalledWith("线上推理");
  onSuccessRef.current?.({
    id: 1,
    name: "线上推理",
    key: PLAINTEXT,
    key_prefix: "sk-a1b2c3d4",
    last_used_at: null,
    revoked_at: null,
    created_at: "2026-08-27T00:00:00Z",
  });
  return { user, onClose, dialog: await screen.findByRole("dialog") };
}

describe("ApiKeyModal", () => {
  it("未填名称时创建按钮禁用(空名字的 Key 无法辨认用途)", () => {
    render(
      <App>
        <ApiKeyModal uuid="u-1" open onClose={vi.fn()} />
      </App>,
    );
    expect(screen.getByRole("button", { name: /^创\s*建$/ })).toBeDisabled();
    expect(createMutate).not.toHaveBeenCalled();
  });

  it("成功态展示明文全值并给出「关闭后无法再查看」的警告", async () => {
    const { dialog } = await openSuccessState();
    expect(within(dialog).getByText(PLAINTEXT)).toBeInTheDocument();
    expect(within(dialog).getByText("关闭后无法再查看")).toBeInTheDocument();
  });

  it("勾选「我已保存」之前关不掉:关闭按钮禁用,且没有 X 可点", async () => {
    const { user, onClose, dialog } = await openSuccessState();
    const closeBtn = within(dialog).getByRole("button", { name: /已保存,关\s*闭/ });
    expect(closeBtn).toBeDisabled();
    // closable=false:成功态不给右上角 X,避免手滑关掉丢明文
    expect(within(dialog).queryByRole("button", { name: /close/i })).toBeNull();

    await user.click(within(dialog).getByRole("checkbox"));
    expect(closeBtn).toBeEnabled();
    await user.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
