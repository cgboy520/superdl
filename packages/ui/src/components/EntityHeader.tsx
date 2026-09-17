/** Entity header (shared by both consoles): name (inline rename) / status badge / tag row / meta bar (KeyValue inline, copyable ID) / action group; wraps on narrow screens.
 *  Shared by instance / service details, the tenant drawer and the node drawer; breadcrumb or back is provided by the caller via PageContainer. */

import { Card, Grid, Space, Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize, fontWeight, space } from "../tokens";
import { InlineEdit } from "./InlineEdit";
import { KeyValue, type KeyValueItem } from "./KeyValue";

export interface EntityHeaderProps {
  name: string;
  /** Status badge (StatusTag badge) */
  status?: ReactNode;
  /** Tag group right of the name row */
  tags?: ReactNode;
  /** Key information bar */
  meta?: KeyValueItem[];
  /** Action group (RowActions size="middle") */
  actions?: ReactNode;
  /** Inline rename */
  rename?: { onSave: (next: string) => Promise<void>; ariaLabel: string; maxLength?: number };
  /** page = standalone card; drawer = drawer header (no card border, compact) */
  size?: "page" | "drawer";
  /** One-line subtitle under the name (e.g. slug / hostname) */
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
