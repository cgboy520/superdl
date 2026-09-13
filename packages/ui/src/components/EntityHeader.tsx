/** 实体头(两端统一):名称(可行内改名)/ 状态徽标 / 标签行 / 元信息条(KeyValue inline,ID 可复制)/ 操作组;窄屏自动换行。
 *  实例 / 服务详情、租户抽屉、节点抽屉共用;面包屑或返回由调用方经 PageContainer 提供。 */

import { Card, Grid, Space, Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize, fontWeight, space } from "../tokens";
import { InlineEdit } from "./InlineEdit";
import { KeyValue, type KeyValueItem } from "./KeyValue";

export interface EntityHeaderProps {
  name: string;
  /** 状态徽标(StatusTag badge) */
  status?: ReactNode;
  /** 名称行右侧的标签组 */
  tags?: ReactNode;
  /** 关键信息条 */
  meta?: KeyValueItem[];
  /** 操作组(RowActions size="middle") */
  actions?: ReactNode;
  /** 行内改名 */
  rename?: { onSave: (next: string) => Promise<void>; ariaLabel: string; maxLength?: number };
  /** page = 独立卡;drawer = 抽屉头(无卡边,紧凑) */
  size?: "page" | "drawer";
  /** 名称下方的一句副标题(如 slug / 主机名) */
  subtitle?: ReactNode;
}

export function EntityHeader({
  name,
  status,
  tags,
  meta,
  actions,
  rename,
  size = "page",
  subtitle,
}: EntityHeaderProps) {
  const screens = Grid.useBreakpoint();
  const wide = screens.md ?? true;
  const title = (
    <Typography.Title level={4} style={{ margin: 0, fontSize: fontSize.pageTitle, fontWeight: fontWeight.semibold }}>
      {name}
    </Typography.Title>
  );
  const body = (
    <div style={{ display: "flex", flexDirection: "column", gap: space.md }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: space.md,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: space.xs, minWidth: 0 }}>
          <Space size={space.sm} wrap align="center">
            {rename ? (
              <InlineEdit
                value={name}
                onSave={rename.onSave}
                ariaLabel={rename.ariaLabel}
                maxLength={rename.maxLength}
                size="middle"
                trigger="always"
                display={title}
              />
            ) : (
              title
            )}
            {status}
          </Space>
          {subtitle && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {subtitle}
            </Typography.Text>
          )}
          {tags && (
            <Space size={space.xs} wrap>
              {tags}
            </Space>
          )}
        </div>
        {actions && <div style={wide ? undefined : { width: "100%" }}>{actions}</div>}
      </div>
      {meta && meta.length > 0 && <KeyValue items={meta} layout="inline" size="small" />}
    </div>
  );
  if (size === "drawer") return body;
  return <Card>{body}</Card>;
}
