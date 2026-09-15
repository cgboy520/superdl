/** 页头(两端统一):面包屑 / 返回 → 标题 + 描述 → 右侧动作;可选「数据新鲜度」行(Freshness)。
 *  路由链接由调用方渲染后经 breadcrumb 传入(本包不依赖路由库)。 */

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Breadcrumb, Button, Space, Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize, space } from "../tokens";
import { Freshness, type FreshnessProps } from "./Freshness";

export type { FreshnessProps } from "./Freshness";

export interface PageHeaderProps {
  /** 不传则不渲染标题行(只有面包屑 / 返回的页面:详情页由 EntityHeader 承担标题) */
  title?: ReactNode;
  /** 标题下方的描述。 */
  description?: ReactNode;
  /** 右侧动作区(主按钮 + 次按钮) */
  extra?: ReactNode;
  /** 面包屑:除最后一项外由调用方渲染为链接;传入即渲染,最后一项默认当前页 */
  breadcrumb?: ReactNode[];
  /** 返回上一级(与 breadcrumb 二选一) */
  back?: { label: ReactNode; onClick: () => void };
  freshness?: FreshnessProps;
  /** 标题右侧的徽标/标签组 */
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
