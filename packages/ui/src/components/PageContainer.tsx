/** 页面容器:控制台各页统一的「最大宽度居中 + 标题行」骨架。
 *  收敛各路由页重复的 maxWidth/margin/标题行 inline style(见 layout/fontSize token)。
 *  width 三档对应 layout 页宽 token:default=控制台 1280 / wide=落地页 1200 / narrow=长文页 880。
 */

import { Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize, layout, space } from "../tokens";

const widthMap = {
  default: layout.pageMaxWidth,
  wide: layout.pageMaxWidthWide,
  narrow: layout.pageMaxWidthNarrow,
} as const;

export function PageContainer({
  title,
  extra,
  width = "default",
  children,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  /** 页宽档位(对应 layout 三档页宽 token) */
  width?: keyof typeof widthMap;
  children: ReactNode;
}) {
  return (
    <div style={{ maxWidth: widthMap[width], margin: "0 auto" }}>
      {title !== undefined && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: space.md,
            marginBottom: space.lg,
          }}
        >
          <Typography.Title level={4} style={{ margin: 0, fontSize: fontSize.pageTitle }}>
            {title}
          </Typography.Title>
          {extra}
        </div>
      )}
      {children}
    </div>
  );
}
