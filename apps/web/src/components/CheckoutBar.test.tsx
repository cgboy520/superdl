/** Narrow-screen checkout bar: detail expansion and pending-balance rendering. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CheckoutBar } from "./CheckoutBar";

describe("CheckoutBar", () => {
  it("narrow screen: the price figure and primary button stay on one line, details go into the bottom sheet", async () => {
    render(
      <CheckoutBar
        items={[{ label: "配置费用", value: "¥2.50/时", suffix: "× 1 卡" }]} // cjk-ok
        actions={<button>创建并开机</button>} // cjk-ok
      />,
    );
    expect(screen.getByText("¥2.50/时")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByText("× 1 卡")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByRole("button", { name: "创建并开机" })).toBeInTheDocument(); // cjk-ok
    expect(screen.queryByText("配置费用")).not.toBeInTheDocument(); // cjk-ok
    await userEvent.click(screen.getByRole("button", { name: /明细/ })); // cjk-ok
    expect(await screen.findByText("配置费用")).toBeInTheDocument(); // cjk-ok
  });

  it("error path: a pending balance renders — instead of a fake ¥0.00", async () => {
    render(<CheckoutBar items={[]} balance={null} balanceReady={false} actions={<button />} />);
    await userEvent.click(screen.getByRole("button", { name: /明细/ })); // cjk-ok
    expect(await screen.findByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/¥0\.00/)).not.toBeInTheDocument();
  });
});
