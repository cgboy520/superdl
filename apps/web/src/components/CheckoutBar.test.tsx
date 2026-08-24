/**
 * CheckoutBar 组件测试:费用项渲染、余额格式化、错误路径(余额未就绪绝不渲染假 ¥0.00)、
 * 费用明细 Popover 交互。
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CheckoutBar } from "./CheckoutBar";

describe("CheckoutBar", () => {
  it("渲染汇总块、费用项与操作区", () => {
    render(
      <CheckoutBar
        summary="RTX4090 × 1"
        items={[{ label: "配置费用", value: "¥1.68/时" }]}
        actions={<button>立即创建</button>}
      />,
    );
    expect(screen.getByText("RTX4090 × 1")).toBeInTheDocument();
    expect(screen.getByText("配置费用")).toBeInTheDocument();
    expect(screen.getByText("¥1.68/时")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "立即创建" })).toBeEnabled();
  });

  it("余额就绪时按货币格式化展示", () => {
    render(<CheckoutBar items={[]} balance="12.34" balanceReady actions={<button />} />);
    expect(screen.getByText("¥12.34")).toBeInTheDocument();
  });

  it("错误路径:余额未就绪渲染 — 而不是假 ¥0.00(查询失败 data 恒为 undefined)", () => {
    render(<CheckoutBar items={[]} balance={null} balanceReady={false} actions={<button />} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/¥0\.00/)).not.toBeInTheDocument();
  });

  it("费用明细:悬停出现 Popover 内容", async () => {
    const user = userEvent.setup();
    render(
      <CheckoutBar
        items={[]}
        detail={<div>日常费用 ¥0.10/时</div>}
        actions={<button />}
      />,
    );
    await user.hover(screen.getByText("费用明细"));
    expect(await screen.findByText("日常费用 ¥0.10/时")).toBeInTheDocument();
  });
});
