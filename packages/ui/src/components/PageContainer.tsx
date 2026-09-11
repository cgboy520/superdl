/** 页面容器:最大宽度居中 + 标题行。width 三档:default 1280 / wide 1200 / narrow 880。 */

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
  /** 页宽档位 */
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
