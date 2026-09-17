/** Data-disk deletion: name input and data-erasure checkbox. */
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
  it("unlocks delete only once the disk name is typed and the erasure box is ticked", async () => {
    const user = userEvent.setup();
    render(
      <App>
        <DeleteDiskModal disk={DISK} onClose={vi.fn()} />
      </App>,
    );
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认删除" }); // cjk-ok
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("train-data"), "train-data");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(deleteMutate).toHaveBeenCalledWith("d-1");
  });
});
