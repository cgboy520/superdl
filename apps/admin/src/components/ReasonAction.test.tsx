/** ReasonAction 状态机:二次确认取消回第一步且原因保留;提交在途禁止关闭。react-dom/client + act 直驱 jsdom。 */

import { App, ConfigProvider } from "antd";
import i18n from "i18next";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import zhCNAdmin from "../locales/zh-CN/admin.json";
import { ReasonAction } from "./ReasonAction";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(async () => {
  // 文案取真实 locales
  await i18n.use(initReactI18next).init({
    lng: "zh-CN",
    resources: { "zh-CN": { admin: zhCNAdmin } },
    defaultNS: "admin",
    returnNull: false,
  });
});

let container: HTMLDivElement;
let root: Root;

function text(el: Element | null | undefined): string {
  return (el?.textContent ?? "").replace(/\s/g, "");
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.body.querySelectorAll("button")].find((b) => text(b) === label);
}

function click(el: Element) {
  act(() => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30));
  });
}

/** 轮询等终态 */
async function waitFor(cond: () => boolean, timeoutMs = 3000): Promise<void> {
  const start = Date.now();
  for (;;) {
    await flush();
    if (cond()) return;
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor 超时:条件未达成");
    }
  }
}

function renderAction(onSubmit: (reason: string) => Promise<string | void>) {
  act(() => {
    root.render(
      <I18nextProvider i18n={i18n}>
        {/* 关掉 motion:jsdom 不跑 transition */}
        <ConfigProvider theme={{ token: { motion: false } }}>
          <App>
            <ReasonAction
              label="下架"
              title="下架 SKU"
              confirmText="下架后不可新租,已有实例不受影响"
              danger
              disabledReason="只读角色不可操作"
              onSubmit={onSubmit}
            />
          </App>
        </ConfigProvider>
      </I18nextProvider>,
    );
  });
}

function setTextareaValue(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
});

describe("ReasonAction", () => {
  it("第二步取消返回第一步,已填原因保留", async () => {
    renderAction(vi.fn().mockResolvedValue(undefined));
    // 打开第一步(原因弹窗)
    click(findButton("下架")!);
    await flush();
    const textarea = document.body.querySelector("textarea");
    expect(textarea).not.toBeNull();
    setTextareaValue(textarea!, "滞销规格下架");
    // 进入第二步
    click(findButton("下一步")!);
    await flush();
    expect(document.body.textContent).toContain("下架后不可新租");
    // 第一步 destroyOnHidden,原因输入框已卸载
    await waitFor(() => document.body.querySelector("textarea") === null);
    // 第二步取消 → 返回第一步,原因还在;取消钮文案是 Cancel(未配中文 locale)
    const cancelBtn = [...document.body.querySelectorAll(".ant-modal-footer button")].find(
      (b) => !b.classList.contains("ant-btn-primary"),
    );
    click(cancelBtn!);
    await waitFor(() => document.body.querySelector("textarea") !== null);
    const reopened = document.body.querySelector("textarea");
    expect(reopened!.value).toBe("滞销规格下架");
  });

  it("提交飞行中蒙层点击不关闭;完成后关闭并提示", async () => {
    let resolveSubmit: (() => void) | null = null;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        }),
    );
    renderAction(onSubmit);
    click(findButton("下架")!);
    await flush();
    setTextareaValue(document.body.querySelector("textarea")!, "滞销规格下架");
    click(findButton("下一步")!);
    await flush();
    // 确认执行 → 请求在途
    click(findButton("确认执行")!);
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("滞销规格下架");
    // 在途时点蒙层不关
    const wrap = [...document.body.querySelectorAll<HTMLElement>(".ant-modal-wrap")].find((w) =>
      w.textContent?.includes("下架后不可新租"),
    );
    click(wrap!);
    await flush();
    expect(document.body.textContent).toContain("下架后不可新租");
    // 请求完成 → 弹窗关闭
    await act(async () => {
      resolveSubmit!();
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => {
      const w = [...document.body.querySelectorAll<HTMLElement>(".ant-modal-wrap")].find((el) =>
        el.textContent?.includes("下架后不可新租"),
      );
      return !w || w.style.display === "none";
    });
  });
});
