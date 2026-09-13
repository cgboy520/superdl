/** 门控按钮:reason 非空 = 可见但不可用(aria-disabled + 可聚焦 + 拦截点击 + Tooltip 原因);reason 为空 = 普通按钮。
 *  antd 6 的原生 disabled 按钮无鼠标事件、不可聚焦,Tooltip 弹不出原因;条件禁用一律用本件(ui-ux-spec §1 规则 4)。 */

import { Button, Tooltip, type ButtonProps, type TooltipProps } from "antd";
import type { MouseEvent, ReactNode } from "react";

export interface GatedButtonProps extends Omit<ButtonProps, "disabled"> {
  /** 不可用原因;非空即门控 */
  reason?: ReactNode;
  /** 硬禁用(无原因,仅提交在途等瞬态);有 reason 时忽略 */
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
