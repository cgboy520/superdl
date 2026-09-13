/** 删除数据盘的两道闸(ui-ux-spec §1 规则 7)。挂了说明:只键入盘名就能删盘,用户会在没意识到数据被清除的情况下确认。写操作 hook 全 mock。 */
import type { DiskOut } from "@superdl/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeleteDiskModal } from "./_console.storage";

const { deleteMutate } = vi.hoisted(() => ({ deleteMutate: vi.fn() }));

vi.mock("../api/mutations", () => ({
  useCreateDisk: () => ({ mutate: vi.fn(), isPending: false }),
  useExpandDisk: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDisk: () => ({ mutate: deleteMutate, isPending: false }),
}));

const DISK = { uuid: "d-1", name: "train-data", size_gb: 500 } as DiskOut;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DeleteDiskModal", () => {
  it("键入盘名 + 勾选数据清除两道闸都过才解锁删除", async () => {
    const user = userEvent.setup();
    render(
      <App>
        <DeleteDiskModal disk={DISK} onClose={vi.fn()} />
      </App>,
    );
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认删除" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("train-data"), "train-data");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(deleteMutate).toHaveBeenCalledWith("d-1");
  });
});
