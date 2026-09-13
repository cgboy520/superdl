/** 实心底状态 Tag(两端共用):十六进制色用内联样式压成实心底,字色按底色亮度取白 / 深墨;antd preset 色名原样透传。 */

import { Tag } from "antd";
import type { CSSProperties, ReactNode } from "react";

import { textOnColor } from "../color";

const solidStyle = (color: string): CSSProperties => ({
  backgroundColor: color,
  borderColor: "transparent",
  color: textOnColor(color),
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
