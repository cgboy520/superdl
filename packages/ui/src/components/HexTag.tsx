/** 实心底状态 Tag(两端共用):十六进制 token 直接当 antd color 用时,深色主题会把
 *  非 preset 色调亮成白字近白底(~3:1)。十六进制一律用内联样式压成深底白字
 *  (token 取值已过 WCAG AA ≥4.5:1);antd preset 色名原样透传。
 *  收编原 admin StatusTag 的 solidStyle 模式,供 web 暗色主题复用同一防御。
 */

import { Tag } from "antd";
import type { CSSProperties, ReactNode } from "react";

import { textOnAccent } from "../tokens";

// 深底白字:背景取 token 原值,文字恒白
const solidStyle = (color: string): CSSProperties => ({
  backgroundColor: color,
  borderColor: "transparent",
  color: textOnAccent,
});

export function HexTag({ color, children }: { color?: string; children: ReactNode }) {
  if (!color) return <Tag>{children}</Tag>;
  if (color.startsWith("#")) {
    return (
      <Tag color={color} style={solidStyle(color)}>
        {children}
      </Tag>
    );
  }
  return <Tag color={color}>{children}</Tag>;
}
