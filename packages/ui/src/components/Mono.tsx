/** Monospace span; truncate keeps the first N characters and puts the full value in title. */

import type { CSSProperties } from "react";

export function Mono({
  children,
  truncate,
  block,
  className,
  style,
}: {
  children: string;
  /** Show only the first N characters, full value in title */
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
