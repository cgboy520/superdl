/** CheckoutBar 组件测试:余额未就绪绝不渲染假 ¥0.00(查询失败 data 恒为 undefined)。 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CheckoutBar } from "./CheckoutBar";

describe("CheckoutBar", () => {
  it("错误路径:余额未就绪渲染 — 而不是假 ¥0.00", () => {
    render(<CheckoutBar items={[]} balance={null} balanceReady={false} actions={<button />} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/¥0\.00/)).not.toBeInTheDocument();
  });
});
