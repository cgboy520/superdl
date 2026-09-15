/** ReasonAction 状态机:二次确认取消回第一步且原因保留;提交在途禁止关闭;confirm={false} 只填原因直提。
 *  react-dom/client + act 直驱 jsdom。 */

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

function renderAction(onSubmit: (reason: string) => Promise<string> | Promise<void>, confirm?: boolean) {
  act(() => {
    root.render(
      <I18nextProvider i18n={i18n}>
        <ConfigProvider theme={{ token: { motion: false } }}>
          <App>
            <ReasonAction
              label="下架"
              title="下架 SKU"
              confirmText="下架后不可新租,已有实例不受影响"
              danger
              disabledReason="只读角色不可操作"
              confirm={confirm}
              onSubmit={onSubmit}
            />
          </App>
        </ConfigProvider>
      </I18nextProvider>,
    );
  });
}

function setTextareaValue(el: HTMLTextAreaElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** 缺失值使测试失败。 */
function must<T>(v: T | null | undefined): T {
  if (v == null) throw new Error("expected element to exist");
  return v;
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
    click(must(findButton("下架")));
    await flush();
    setTextareaValue(must(document.body.querySelector("textarea")), "滞销规格下架");
    click(must(findButton("下一步")));
    await flush();
    expect(document.body.textContent).toContain("下架后不可新租");
    await waitFor(() => document.body.querySelector("textarea") === null);
    const cancelBtn = [...document.body.querySelectorAll(".ant-modal-footer button")].find(
      (b) => !b.classList.contains("ant-btn-primary"),
    );
    click(must(cancelBtn));
    await waitFor(() => document.body.querySelector("textarea") !== null);
    expect(must(document.body.querySelector("textarea")).value).toBe("滞销规格下架");
  });

  it("confirm={false} 填完原因直接提交,不出二次确认", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderAction(onSubmit, false);
    click(must(findButton("下架")));
    await flush();
    setTextareaValue(must(document.body.querySelector("textarea")), "节点已恢复");
    expect(findButton("下一步")).toBeUndefined();
    click(must(findButton("确认执行")));
    await waitFor(() => onSubmit.mock.calls.length > 0);
    expect(onSubmit).toHaveBeenCalledWith("节点已恢复");
    expect(document.body.textContent).not.toContain("二次确认");
    expect(document.body.textContent).not.toContain("下架后不可新租");
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
    click(must(findButton("下架")));
    await flush();
    setTextareaValue(must(document.body.querySelector("textarea")), "滞销规格下架");
    click(must(findButton("下一步")));
    await flush();
    click(must(findButton("确认执行")));
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("滞销规格下架");
    const wrap = [...document.body.querySelectorAll<HTMLElement>(".ant-modal-wrap")].find((w) =>
      w.textContent.includes("下架后不可新租"),
    );
    click(must(wrap));
    await flush();
    expect(document.body.textContent).toContain("下架后不可新租");
    await act(async () => {
      must(resolveSubmit)();
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => {
      const w = [...document.body.querySelectorAll<HTMLElement>(".ant-modal-wrap")].find((el) =>
        el.textContent.includes("下架后不可新租"),
      );
      return !w || w.style.display === "none";
    });
  });
});
