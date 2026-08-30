/** EmptyState 场景文案与动作回归。 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { EmptyState } from "./EmptyState";

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh-CN",
    resources: {
      "zh-CN": {
        shared: {
          empty: {
            list: "暂无数据",
            search: "没有符合条件的结果,试试调整筛选",
            notification: "暂无通知",
            disk: "暂无数据盘",
            ticket: "暂无工单",
          },
        },
      },
    },
    defaultNS: "shared",
    returnNull: false,
  });
});

function renderWithI18n(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("EmptyState", () => {
  it("按场景渲染默认文案;description 覆盖默认文案", () => {
    renderWithI18n(<EmptyState scene="search" />);
    expect(screen.getByText("没有符合条件的结果,试试调整筛选")).toBeInTheDocument();
  });

  it("渲染主动作与次动作", async () => {
    const onClear = vi.fn();
    renderWithI18n(
      <EmptyState
        scene="list"
        description="暂无实例"
        action={<button onClick={onClear}>清空筛选</button>}
      />,
    );
    expect(screen.getByText("暂无实例")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "清空筛选" }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
