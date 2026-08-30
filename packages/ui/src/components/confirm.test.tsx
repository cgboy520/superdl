/** confirm 共享件测试:
 *  - TypeConfirmModal(L3):键入 + 勾选双闸缺一不可、省略勾选闸时只过键入、关闭重开状态重置
 *    (重置靠渲染期派生,关了又开还是上一轮的键入 = 破坏确认形同虚设)。
 *  - useConfirm(L2):后果前置列表与影响说明按结构渲染,danger 透传到红色按钮。 */

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
        shared: { confirm: { typeNameToConfirm: "键入 {{name}} 以确认" } },
      },
    },
    defaultNS: "shared",
    returnNull: false,
  });
});

const BASE: Omit<TypeConfirmModalProps, "checkboxLabel" | "onConfirm" | "onCancel"> = {
  open: true,
  title: "释放实例",
  body: "释放后实例盘全部数据立即清除",
  targetName: "demo-vm",
  confirmLabel: "确认释放",
  cancelLabel: "取消",
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

// antd Button 对两字中文自动插空(autoInsertSpace),可访问名是「确 认 释 放」之外的形态需按原样匹配
describe("TypeConfirmModal", () => {
  it("键入匹配 + 勾选两道闸全过才解锁确认按钮", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    renderModal({ checkboxLabel: "我确认将清除实例盘全部数据" }, { onConfirm });
    const dialog = await screen.findByRole("dialog");
    // 键入提示由 shared:confirm.typeNameToConfirm 插值渲染
    expect(within(dialog).getByText("键入 demo-vm 以确认")).toBeInTheDocument();
    const confirmBtn = within(dialog).getByRole("button", { name: "确认释放" });
    expect(confirmBtn).toBeDisabled();
    // 只过键入闸仍锁着
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirmBtn).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirmBtn).toBeEnabled();
    await user.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("省略 checkboxLabel 时只有键入一道闸(不渲染勾选框)", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    const confirmBtn = within(dialog).getByRole("button", { name: "确认释放" });
    expect(confirmBtn).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirmBtn).toBeEnabled();
  });

  it("关闭重开后键入与勾选都重置(全新一轮)", async () => {
    const user = userEvent.setup();
    const tree = (open: boolean) => (
      <I18nextProvider i18n={i18n}>
        <TypeConfirmModal
          {...BASE}
          open={open}
          checkboxLabel="我确认将清除实例盘全部数据"
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
    expect(screen.getByRole("button", { name: "确认释放" })).toBeDisabled();
  });
});

describe("useConfirm", () => {
  function Harness({ onOk }: { onOk: () => void }) {
    const confirm = useConfirm();
    return (
      <Button
        onClick={() =>
          confirm({
            title: "确认关机?",
            consequences: ["GPU 立即释放,再开机可能库存不足"],
            impact: "该操作影响 3 台在跑实例",
            okText: "确认关机",
            cancelText: "取消",
            danger: true,
            onOk,
          })
        }
      >
        触发
      </Button>
    );
  }

  it("渲染后果列表与影响说明,danger 透传红色按钮,确认触发 onOk", async () => {
    const user = userEvent.setup();
    const onOk = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <App>
          <Harness onOk={onOk} />
        </App>
      </I18nextProvider>,
    );
    // 两字中文按钮可访问名带自动空格(「触 发」)
    await user.click(screen.getByRole("button", { name: /触\s*发/ }));
    const dialog = await screen.findByRole("dialog");
    // antd v6 confirm 标题渲染两处(.ant-modal-title 与 .ant-modal-confirm-title)
    expect(within(dialog).getAllByText("确认关机?").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("GPU 立即释放,再开机可能库存不足")).toBeInTheDocument();
    expect(within(dialog).getByText("该操作影响 3 台在跑实例")).toBeInTheDocument();
    const okBtn = within(dialog).getByRole("button", { name: "确认关机" });
    expect(okBtn.className).toContain("ant-btn-dangerous");
    await user.click(okBtn);
    expect(onOk).toHaveBeenCalledTimes(1);
  });
});
