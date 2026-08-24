/**
 * 筛选链 chip 行(市场/创建页)。单选;禁用项可见但灰置 + tooltip 原因。
 * 「全部」这类聚合项由调用方用哨兵值表达。
 */

import { brand, colorPrimary } from "@superdl/ui";
import { Button, Space, theme, Tooltip, Typography } from "antd";
import { useId, type ReactNode } from "react";

export interface ChipOption<T extends string | number> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
}

export function ChipRow<T extends string | number>({
  label,
  options,
  value,
  onChange,
  extra,
}: {
  label: string;
  options: ChipOption<T>[];
  value: T;
  onChange: (v: T) => void;
  extra?: ReactNode;
}) {
  const { token } = theme.useToken();
  const labelId = useId();
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
      <Typography.Text
        type="secondary"
        id={labelId}
        style={{ flexShrink: 0, width: 72, lineHeight: "32px", textAlign: "right" }}
      >
        {label}
      </Typography.Text>
      <Space wrap size={8} style={{ flex: 1 }} role="group" aria-labelledby={labelId}>
        {options.map((o) => {
          const selected = o.value === value;
          const btn = (
            <Button
              key={String(o.value)}
              size="middle"
              disabled={o.disabled}
              aria-pressed={selected}
              onClick={() => onChange(o.value)}
              style={
                selected
                  ? {
                      borderColor: colorPrimary,
                      color: colorPrimary,
                      background: brand.indigo50,
                      fontWeight: 500,
                    }
                  : { borderColor: token.colorBorder }
              }
            >
              {o.label}
            </Button>
          );
          return o.disabled && o.disabledReason ? (
            <Tooltip key={String(o.value)} title={o.disabledReason}>
              {btn}
            </Tooltip>
          ) : (
            btn
          );
        })}
        {extra}
      </Space>
    </div>
  );
}
