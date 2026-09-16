/** Key-value list (shared by both consoles): one fixed configuration of antd Descriptions; empty → EmptyValue, copy → CopyField, mono → Mono. */

import { Descriptions, type DescriptionsProps } from "antd";
import type { ReactNode } from "react";

import { CopyField } from "./CopyField";
import { EmptyValue } from "./EmptyValue";
import { Mono } from "./Mono";

export interface KeyValueItem {
  label: ReactNode;
  value: ReactNode | null | undefined;
  /** When given, a copy button follows the value (copying this string) */
  copy?: string;
  /** The value is an identifier, rendered monospace (value must be a string) */
  mono?: boolean;
  hint?: ReactNode;
  span?: number;
}

export function KeyValue({
  items,
  layout = "grid",
  columns,
  size = "small",
  labelWidth,
}: {
  items: KeyValueItem[];
  layout?: "grid" | "inline" | "vertical";
  columns?: DescriptionsProps["column"];
  size?: "small" | "middle";
  labelWidth?: number;
}) {
  const render = (it: KeyValueItem): ReactNode => {
    if (it.value == null || it.value === "") return <EmptyValue />;
    if (it.copy) return <CopyField value={it.copy} display={it.value} mono={it.mono} />;
    if (it.mono && typeof it.value === "string") return <Mono>{it.value}</Mono>;
    return it.value;
  };
  return (
    <Descriptions
      size={size}
      layout={layout === "vertical" ? "vertical" : "horizontal"}
      column={columns ?? (layout === "inline" ? { xs: 1, sm: 2, md: 3, xl: 4 } : 1)}
      styles={labelWidth ? { label: { width: labelWidth } } : undefined}
      items={items.map((it, i) => ({
        key: i,
        label: it.hint ? <span title={typeof it.hint === "string" ? it.hint : undefined}>{it.label}</span> : it.label,
        children: render(it),
        span: it.span,
      }))}
    />
  );
}
