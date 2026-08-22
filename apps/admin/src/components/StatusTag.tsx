/**
 * 状态 Tag:packages/ui 状态表带的是十六进制色,antd 6 深色主题下非 preset 色的
 * filled 药丸亮度被拉高,白字近白底(实测对比度 ~3:1 不达标)。
 * 管理端本地把已知十六进制映射回 antd preset 色名(深浅主题都由 antd 保证对比度);
 * 表外十六进制回落 outlined(描边+着色文字,不填底)。预设色名原样透传。
 */

import { Tag } from "antd";
import type { ReactNode } from "react";

const HEX_TO_PRESET: Record<string, string> = {
  "#16a34a": "green", // statusColors.green
  "#2563eb": "blue", // statusColors.blue
  "#9ca3af": "default", // statusColors.gray
  "#ea580c": "orange", // statusColors.orange
  "#dc2626": "red", // statusColors.red
  "#4f46e5": "geekblue", // skuTierMap.dedicated
  "#0891b2": "cyan", // skuTierMap.mig
};

export function StatusTag({ color, children }: { color?: string; children: ReactNode }) {
  if (!color) return <Tag>{children}</Tag>;
  const preset = HEX_TO_PRESET[color.toLowerCase()];
  if (preset) return <Tag color={preset}>{children}</Tag>;
  if (color.startsWith("#")) {
    return (
      <Tag color={color} variant="outlined">
        {children}
      </Tag>
    );
  }
  return <Tag color={color}>{children}</Tag>;
}
