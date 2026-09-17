/** Solid-background status Tag (shared by both consoles): hex colours become a solid background via inline style, text colour white / deep ink by background luminance; antd preset colour names pass through. */

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
