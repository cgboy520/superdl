/** ReasonAction state machine: cancelling the second confirmation returns to step one with the reason kept; no closing while the submit is in flight; confirm={false} submits with the reason alone.
 *  react-dom/client + act drive jsdom directly. */

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

/** Poll until the terminal state */
async function waitFor(cond: () => boolean, timeoutMs = 3000): Promise<void> {
  const start = Date.now();
  for (;;) {
    await flush();
    if (cond()) return;
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor timed out: condition not met");
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
              label="Delist"
              title="Delist SKU"
              confirmText="Cannot be rented after delisting; existing instances are unaffected"
              danger
              disabledReason="Read-only roles cannot operate"
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

/** A missing value fails the test. */
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
  it("cancelling step two returns to step one with the typed reason kept", async () => {
    renderAction(vi.fn().mockResolvedValue(undefined));
    click(must(findButton("Delist")));
    await flush();
    setTextareaValue(must(document.body.querySelector("textarea")), "slow-moving spec delisted");
    click(must(findButton("下一步"))); // cjk-ok
    await flush();
    expect(document.body.textContent).toContain("Cannot be rented after delisting");
    await waitFor(() => document.body.querySelector("textarea") === null);
    const cancelBtn = [...document.body.querySelectorAll(".ant-modal-footer button")].find(
      (b) => !b.classList.contains("ant-btn-primary"),
    );
    click(must(cancelBtn));
    await waitFor(() => document.body.querySelector("textarea") !== null);
    expect(must(document.body.querySelector("textarea")).value).toBe("slow-moving spec delisted");
  });

  it("confirm={false} submits straight after the reason, no second confirmation", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderAction(onSubmit, false);
    click(must(findButton("Delist")));
    await flush();
    setTextareaValue(must(document.body.querySelector("textarea")), "node recovered");
    expect(findButton("下一步")).toBeUndefined(); // cjk-ok
    click(must(findButton("确认执行"))); // cjk-ok
    await waitFor(() => onSubmit.mock.calls.length > 0);
    expect(onSubmit).toHaveBeenCalledWith("node recovered");
    expect(document.body.textContent).not.toContain("二次确认"); // cjk-ok
    expect(document.body.textContent).not.toContain("Cannot be rented after delisting");
  });

  it("clicking the mask while the submit is in flight does not close; closes with a message when done", async () => {
    let resolveSubmit: (() => void) | null = null;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        }),
    );
    renderAction(onSubmit);
    click(must(findButton("Delist")));
    await flush();
    setTextareaValue(must(document.body.querySelector("textarea")), "slow-moving spec delisted");
    click(must(findButton("下一步"))); // cjk-ok
    await flush();
    click(must(findButton("确认执行"))); // cjk-ok
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("slow-moving spec delisted");
    const wrap = [...document.body.querySelectorAll<HTMLElement>(".ant-modal-wrap")].find((w) =>
      w.textContent.includes("Cannot be rented after delisting"),
    );
    click(must(wrap));
    await flush();
    expect(document.body.textContent).toContain("Cannot be rented after delisting");
    await act(async () => {
      must(resolveSubmit)();
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => {
      const w = [...document.body.querySelectorAll<HTMLElement>(".ant-modal-wrap")].find((el) =>
        el.textContent.includes("Cannot be rented after delisting"),
      );
      return !w || w.style.display === "none";
    });
  });
});
