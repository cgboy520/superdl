/** 窄屏结算条的明细展开与余额未就绪展示测试。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CheckoutBar } from "./CheckoutBar";

describe("CheckoutBar", () => {
  it("窄屏:价格大字与主按钮常驻一行,明细进底部 sheet", async () => {
    render(
      <CheckoutBar
        items={[{ label: "配置费用", value: "¥2.50/时", suffix: "× 1 卡" }]}
        actions={<button>创建并开机</button>}
      />,
    );
    expect(screen.getByText("¥2.50/时")).toBeInTheDocument();
    expect(screen.getByText("× 1 卡")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建并开机" })).toBeInTheDocument();
    expect(screen.queryByText("配置费用")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /明细/ }));
    expect(await screen.findByText("配置费用")).toBeInTheDocument();
  });

  it("错误路径:余额未就绪渲染 — 而不是假 ¥0.00", async () => {
    render(<CheckoutBar items={[]} balance={null} balanceReady={false} actions={<button />} />);
    await userEvent.click(screen.getByRole("button", { name: /明细/ }));
    expect(await screen.findByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/¥0\.00/)).not.toBeInTheDocument();
  });
});
