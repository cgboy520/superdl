/** Shared confirm components: TypeConfirmModal needs both gates, omitting the checkbox leaves only typing, reopening resets; useConfirm renders consequences and impact structurally, danger passes through. */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, Button } from "antd";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { TypeConfirmModal, useConfirm, type TypeConfirmModalProps } from "./confirm";

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh-CN",
    resources: {
      "zh-CN": {
        shared: { confirm: { typeNameToConfirm: "Type {{name}} to confirm" } },
      },
    },
    defaultNS: "shared",
    returnNull: false,
  });
});

const BASE: Omit<TypeConfirmModalProps, "checkboxLabel" | "onConfirm" | "onCancel"> = {
  open: true,
  title: "Release instance",
  body: "All data on the instance disk is erased at once after release",
  targetName: "demo-vm",
  confirmLabel: "Confirm release",
  cancelLabel: "Cancel",
};

function renderModal(
  props: Partial<TypeConfirmModalProps> = {},
  handlers: { onConfirm?: () => void; onCancel?: () => void } = {},
) {
  const merged: TypeConfirmModalProps = {
    ...BASE,
    onConfirm: handlers.onConfirm ?? (() => {}),
    onCancel: handlers.onCancel ?? (() => {}),
    ...props,
  };
  return render(
    <I18nextProvider i18n={i18n}>
      <TypeConfirmModal {...merged} />
    </I18nextProvider>,
  );
}

describe("TypeConfirmModal", () => {
  it("unlocks the confirm button only once typing matches and the checkbox is ticked", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    renderModal({ checkboxLabel: "I confirm all data on the instance disk will be erased" }, { onConfirm });
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Type demo-vm to confirm")).toBeInTheDocument();
    const confirmBtn = within(dialog).getByRole("button", { name: "Confirm release" });
    expect(confirmBtn).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirmBtn).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirmBtn).toBeEnabled();
    await user.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("without checkboxLabel only the typing gate remains (no checkbox rendered)", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    const confirmBtn = within(dialog).getByRole("button", { name: "Confirm release" });
    expect(confirmBtn).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirmBtn).toBeEnabled();
  });

  it("resets typing and checkbox after close and reopen (a fresh round)", async () => {
    const user = userEvent.setup();
    const tree = (open: boolean) => (
      <I18nextProvider i18n={i18n}>
        <TypeConfirmModal
          {...BASE}
          open={open}
          checkboxLabel="I confirm all data on the instance disk will be erased"
          onConfirm={() => {}}
          onCancel={() => {}}
        />
      </I18nextProvider>
    );
    const { rerender } = render(tree(true));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    await user.click(within(dialog).getByRole("checkbox"));
    rerender(tree(false));
    rerender(tree(true));
    const input = await screen.findByPlaceholderText("demo-vm");
    expect(input).toHaveValue("");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Confirm release" })).toBeDisabled();
  });
});

describe("useConfirm", () => {
  function Harness({ onOk }: { onOk: () => void }) {
    const confirm = useConfirm();
    return (
      <Button
        onClick={() =>
          confirm({
            title: "Stop the instance?",
            consequences: ["The GPU is released at once, stock may be short at the next start"],
            impact: "This affects 3 running instances",
            okText: "Confirm stop",
            cancelText: "Cancel",
            danger: true,
            onOk,
          })
        }
      >
        trigger
      </Button>
    );
  }

  it("renders consequences and impact, danger yields a red button, confirm fires onOk", async () => {
    const user = userEvent.setup();
    const onOk = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <App>
          <Harness onOk={onOk} />
        </App>
      </I18nextProvider>,
    );
    await user.click(screen.getByRole("button", { name: /trigger/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText("Stop the instance?").length).toBeGreaterThan(0);
    expect(
      within(dialog).getByText("The GPU is released at once, stock may be short at the next start"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("This affects 3 running instances")).toBeInTheDocument();
    const okBtn = within(dialog).getByRole("button", { name: "Confirm stop" });
    expect(okBtn.className).toContain("ant-btn-dangerous");
    await user.click(okBtn);
    expect(onOk).toHaveBeenCalledTimes(1);
  });
});
