/** TableErrorEmpty:错误态渲染「加载失败 + 重试」,非错误态退回 antd 默认空态。
 *  挂了 = 查询失败又被渲染成「没有数据」。 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { TableErrorEmpty } from "./TableErrorEmpty";

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh-CN",
    resources: {
      "zh-CN": {
        shared: { common: { loadFailed: "加载失败,请重试", retry: "重试" } },
      },
    },
    defaultNS: "shared",
    returnNull: false,
  });
});

function renderEmpty(props: { isError: boolean; onRetry?: () => void }) {
  return render(
    <I18nextProvider i18n={i18n}>
      <TableErrorEmpty {...props} />
    </I18nextProvider>
  );
}

describe("TableErrorEmpty", () => {
  it("错误态渲染加载失败与重试按钮,点击触发 onRetry", async () => {
    const onRetry = vi.fn();
    renderEmpty({ isError: true, onRetry });
    expect(screen.getByText("加载失败,请重试")).toBeInTheDocument();
    const btn = screen.getByRole("button");
    // antd v6 Button 对相邻汉字自动插空格(「重 试」),断言先归一化空白
    expect(btn.textContent?.replace(/\s/g, "")).toBe("重试");
    await userEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("非错误态退回 antd 默认空态(无失败文案、无重试按钮)", () => {
    renderEmpty({ isError: false });
    expect(screen.queryByText("加载失败,请重试")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("无 onRetry 时不渲染重试按钮", () => {
    renderEmpty({ isError: true });
    expect(screen.getByText("加载失败,请重试")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
