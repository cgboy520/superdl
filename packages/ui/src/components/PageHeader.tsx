/** Page header (shared by both consoles): breadcrumb / back → title + description → actions on the right; optional "data freshness" row (Freshness).
 *  Route links are rendered by the caller and passed via breadcrumb (this package has no router dependency). */

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Breadcrumb, Button, Space, Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize, space } from "../tokens";
import { Freshness, type FreshnessProps } from "./Freshness";

export type { FreshnessProps } from "./Freshness";

export interface PageHeaderProps {
  /** Omitted = no title row (pages with only breadcrumb / back: detail pages carry the title in EntityHeader) */
  title?: ReactNode;
  /** Description under the title. */
  description?: ReactNode;
  /** Action area on the right (primary + secondary buttons) */
  extra?: ReactNode;
  /** Breadcrumb: every item but the last is rendered as a link by the caller; rendered when passed, the last item is the current page by default */
  breadcrumb?: ReactNode[];
  /** Back to the parent (mutually exclusive with breadcrumb) */
  back?: { label: ReactNode; onClick: () => void };
  freshness?: FreshnessProps;
  /** Badge / tag group right of the title */
  tags?: ReactNode;
}

export function PageHeader({ title, description, extra, breadcrumb, back, freshness, tags }: PageHeaderProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: space.sm, marginBottom: space.lg }}>
      {breadcrumb && breadcrumb.length > 0 && (
        <Breadcrumb items={breadcrumb.map((node, i) => ({ key: i, title: node }))} />
      )}
      {back && !breadcrumb && (
        <div>
          <Button
            type="text"
            size="small"
            icon={<ArrowLeftOutlined />}
            onClick={back.onClick}
            style={{ paddingInline: 4 }}
          >
            {back.label}
          </Button>
        </div>
      )}
      {(title !== undefined || tags || extra || description || freshness) && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            flexWrap: "wrap",
            gap: space.md,
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: space.xs, minWidth: 0 }}>
            {(title !== undefined || tags) && (
              <Space size={space.sm} wrap align="center">
                {title !== undefined && (
                  <Typography.Title level={4} style={{ margin: 0, fontSize: fontSize.pageTitle }}>
                    {title}
                  </Typography.Title>
                )}
                {tags}
              </Space>
            )}
            {description && (
              <Typography.Text type="secondary" style={{ fontSize: fontSize.body }}>
                {description}
              </Typography.Text>
            )}
            {freshness && <Freshness {...freshness} />}
          </div>
          {extra && (
            <Space size={space.sm} wrap>
              {extra}
            </Space>
          )}
        </div>
      )}
    </div>
  );
}
