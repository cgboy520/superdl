/**
 * 状态 Tag:packages/ui 状态表带的是十六进制 token,直接当 antd color 用时
 * 深色主题下非 preset 色的药丸会被算法调亮,白字近白底(实测 ~3:1 不达标)。
 * 十六进制一律显式深底白字(内联样式压住算法,两端主题都恒定;token 取值已过
 * WCAG AA ≥4.5:1,见 packages/ui tokens.test.ts);antd preset 色名原样透传。
 */

import { Tag } from "antd";
import type { CSSProperties, ReactNode } from "react";

// 深底白字:背景取 token 原值,文字恒白(token 取值已过 ≥4.5:1,见 packages/ui tokens.test.ts)
const solidStyle = (color: string): CSSProperties => ({
  backgroundColor: color,
  borderColor: "transparent",
  color: "#fff",
});

export function StatusTag({ color, children }: { color?: string; children: ReactNode }) {
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
