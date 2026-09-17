/** API key success state: save confirmation and close restriction. */
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

vi.mock("../../api/mutations", () => ({
  useCreateServiceApiKey: (_slug: string, o?: { onSuccess?: (d: unknown) => void }) => {
    onSuccessRef.current = o?.onSuccess;
    return { mutate: createMutate, isPending: false };
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  onSuccessRef.current = undefined;
});

/** "Enter a name → create → the server returns the plaintext", stopping in the success state */
async function openSuccessState(onClose = vi.fn()) {
  const user = userEvent.setup();
  render(
    <App>
      <ApiKeyModal slug="svc-1" open onClose={onClose} />
    </App>,
  );
  await user.type(screen.getByLabelText("名称"), "线上推理"); // cjk-ok
  await user.click(screen.getByRole("button", { name: /^创\s*建$/ })); // cjk-ok
  expect(createMutate).toHaveBeenCalledWith("线上推理"); // cjk-ok
  onSuccessRef.current?.({
    id: 1,
    name: "线上推理", // cjk-ok
    key: PLAINTEXT,
    key_prefix: "sk-a1b2c3d4",
    last_used_at: null,
    revoked_at: null,
    created_at: "2026-08-27T00:00:00Z",
  });
  return { user, onClose, dialog: await screen.findByRole("dialog") };
}

describe("ApiKeyModal", () => {
  it("the create button is disabled without a name (a nameless key cannot be told apart)", () => {
    render(
      <App>
        <ApiKeyModal slug="svc-1" open onClose={vi.fn()} />
      </App>,
    );
    expect(screen.getByRole("button", { name: /^创\s*建$/ })).toBeDisabled(); // cjk-ok
    expect(createMutate).not.toHaveBeenCalled();
  });

  it("the success state shows the full plaintext with the cannot-be-viewed-again warning", async () => {
    const { dialog } = await openSuccessState();
    expect(within(dialog).getByText(PLAINTEXT)).toBeInTheDocument();
    expect(within(dialog).getByText("关闭后无法再查看")).toBeInTheDocument(); // cjk-ok
  });

  it("cannot be closed before ticking saved: the close button is disabled and there is no X", async () => {
    const { user, onClose, dialog } = await openSuccessState();
    const closeBtn = within(dialog).getByRole("button", { name: /已保存,关\s*闭/ }); // cjk-ok
    expect(closeBtn).toBeDisabled();
    expect(within(dialog).queryByRole("button", { name: /close/i })).toBeNull();

    await user.click(within(dialog).getByRole("checkbox"));
    expect(closeBtn).toBeEnabled();
    await user.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
