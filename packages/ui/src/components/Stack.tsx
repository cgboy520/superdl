/** 排版原语:VStack = 全宽纵向 Space(默认 16);Caption = 次要 12px 文字。两端高频写法收敛到这里,避免到处手写 Space + style。 */

import { Space, Typography } from "antd";
import type { CSSProperties, ReactNode } from "react";

import { fontSize, space } from "../tokens";

export function VStack({
  children,
  gap = space.lg,
  style,
}: {
  children: ReactNode;
  /** 间距(px),默认 16 */
  gap?: number;
  style?: CSSProperties;
}) {
  return (
    <Space orientation="vertical" size={gap} style={{ width: "100%", ...style }}>
      {children}
    </Space>
  );
}

export function Caption({
  children,
  type = "secondary",
  block,
  style,
}: {
  children: ReactNode;
  type?: "secondary" | "warning" | "danger";
  block?: boolean;
  style?: CSSProperties;
}) {
  return (
    <Typography.Text type={type} style={{ fontSize: fontSize.caption, display: block ? "block" : undefined, ...style }}>
      {children}
    </Typography.Text>
  );
}
