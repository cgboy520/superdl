/** 等宽 span;truncate 截取前 N 位,完整值放入 title。 */

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
