/** 门控按钮的焦点、原因提示与点击拦截测试。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GatedButton } from "./GatedButton";

describe("GatedButton", () => {
  it("有 reason 时可聚焦、aria-disabled、不触发 onClick", async () => {
    const onClick = vi.fn();
    render(
      <GatedButton reason="仅已关机的实例可以开机" onClick={onClick}>
        开机
      </GatedButton>,
    );
    const btn = screen.getByRole("button", { name: /^开\s*机$/ });
    expect(btn).toHaveAttribute("aria-disabled", "true");
    expect(btn).not.toBeDisabled();
    await userEvent.tab();
    expect(btn).toHaveFocus();
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("无 reason 时是普通按钮,点击生效", async () => {
    const onClick = vi.fn();
    render(<GatedButton onClick={onClick}>开机</GatedButton>);
    await userEvent.click(screen.getByRole("button", { name: /^开\s*机$/ }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
