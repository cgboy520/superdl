/** Focus, reason tooltip and click interception of the gated button. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GatedButton } from "./GatedButton";

describe("GatedButton", () => {
  it("with a reason it stays focusable, is aria-disabled and does not fire onClick", async () => {
    const onClick = vi.fn();
    render(
      <GatedButton reason="Only stopped instances can be started" onClick={onClick}>
        Start
      </GatedButton>,
    );
    const btn = screen.getByRole("button", { name: /^Start$/ });
    expect(btn).toHaveAttribute("aria-disabled", "true");
    expect(btn).not.toBeDisabled();
    await userEvent.tab();
    expect(btn).toHaveFocus();
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("without a reason it is a plain button and the click fires", async () => {
    const onClick = vi.fn();
    render(<GatedButton onClick={onClick}>Start</GatedButton>);
    await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
