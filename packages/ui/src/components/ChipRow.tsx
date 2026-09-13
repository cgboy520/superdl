/** 筛选链 chip 行(市场/创建页)。单选,禁用项走 GatedButton(灰置 + 原因);聚合项由调用方用哨兵值表达。 */

import { useThemeColors } from "../hooks/useThemeColors";
import { fontWeight, space } from "../tokens";
import { GatedButton } from "./GatedButton";
import { Space, theme, Typography } from "antd";
import { useId, type ReactNode } from "react";

export interface ChipOption<T extends string | number> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
}

/** chip 行左侧标签栏宽度(BillingModeCard 数量选择器行同款对齐) */
export const CHIP_LABEL_WIDTH = 84;

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
  // 选中态配色:主色 / 主色浅底(tokens.test 回归的 AA 配对),随主题经 useThemeColors 取
  const colors = useThemeColors();
  const selectedStyle = {
    borderColor: colors.primary,
    color: colors.primary,
    background: colors.primarySoft,
    fontWeight: fontWeight.medium,
  };
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
      <Typography.Text
        type="secondary"
        id={labelId}
        style={{ flexShrink: 0, width: CHIP_LABEL_WIDTH, lineHeight: "32px", textAlign: "right" }}
      >
        {label}
      </Typography.Text>
      <Space wrap size={space.sm} style={{ flex: 1 }} role="group" aria-labelledby={labelId}>
        {options.map((o) => {
          const selected = o.value === value;
          return (
            <GatedButton
              key={String(o.value)}
              size="middle"
              reason={o.disabled ? o.disabledReason : undefined}
              aria-pressed={selected}
              onClick={() => onChange(o.value)}
              style={selected ? selectedStyle : { borderColor: token.colorBorder }}
            >
              {o.label}
            </GatedButton>
          );
        })}
        {extra}
      </Space>
    </div>
  );
}
