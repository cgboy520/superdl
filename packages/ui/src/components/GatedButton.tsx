/** With a non-empty reason it stays focusable, intercepts clicks and shows a Tooltip; otherwise a plain button. */

import { Button, Tooltip, type ButtonProps, type TooltipProps } from "antd";
import type { MouseEvent, ReactNode } from "react";

export interface GatedButtonProps extends Omit<ButtonProps, "disabled"> {
  /** Reason why unavailable; non-empty = gated */
  reason?: ReactNode;
  /** Hard disable (no reason, only transient states such as submission in flight); ignored with a reason */
  disabled?: boolean;
  tooltipPlacement?: TooltipProps["placement"];
}

export function GatedButton({ reason, disabled, tooltipPlacement, onClick, className, ...rest }: GatedButtonProps) {
  if (!reason) {
    return <Button {...rest} disabled={disabled} onClick={onClick} className={className} />;
  }
  const gated = (
    <Button
      {...rest}
      aria-disabled
      tabIndex={0}
      className={className ? `${className} btn-aria-disabled` : "btn-aria-disabled"}
      onClick={(e: MouseEvent<HTMLButtonElement>) => {
        e.preventDefault();
      }}
    />
  );
  return (
    <Tooltip title={reason} placement={tooltipPlacement}>
      {gated}
    </Tooltip>
  );
}
