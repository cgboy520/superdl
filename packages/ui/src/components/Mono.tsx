/** 标识符(UUID / 订单号 / slug / 节点名)等宽渲染;truncate 取前 N 位并把全值放进 title。永不渲染 <code>(e2e 以 code 定位密钥与端点)。 */

import type { CSSProperties } from "react";

export function Mono({
  children,
  truncate,
  block,
  className,
  style,
}: {
  children: string;
  /** 只显示前 N 位,全值进 title */
  truncate?: number;
  block?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  const shown = truncate && children.length > truncate ? children.slice(0, truncate) : children;
  return (
    <span
      className={className ? `mono ${className}` : "mono"}
      title={shown === children ? undefined : children}
      style={block ? { display: "block", ...style } : style}
    >
      {shown}
    </span>
  );
}
