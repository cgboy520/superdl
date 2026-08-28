/** 页面容器:控制台各页统一的「最大宽度居中 + 标题行」骨架。
 *  收敛各路由页重复的 maxWidth/margin/标题行 inline style(见 layout/fontSize token)。
 */

import { Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize, layout, space } from "../tokens";

export function PageContainer({
  title,
  extra,
  children,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div style={{ maxWidth: layout.pageMaxWidth, margin: "0 auto" }}>
      {title !== undefined && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
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
