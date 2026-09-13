/** 键值对列表(两端统一):antd Descriptions 的一份固定配置;空值 → EmptyValue,copy → CopyField,mono → Mono。 */

import { Descriptions, type DescriptionsProps } from "antd";
import type { ReactNode } from "react";

import { CopyField } from "./CopyField";
import { EmptyValue } from "./EmptyValue";
import { Mono } from "./Mono";

export interface KeyValueItem {
  label: ReactNode;
  value: ReactNode | null | undefined;
  /** 传入即在值后出复制按钮(复制该字符串) */
  copy?: string;
  /** 值为标识符,等宽渲染(value 须为字符串) */
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
