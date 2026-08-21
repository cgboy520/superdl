/**
 * 底部通栏结算条(sticky,市场页与创建页共用)。
 * 费用项逐项摊开,「日常费用(关机也产生)」与「配置费用」分栏。
 */

import { brand, colorPrimary } from "@superdl/ui";
import { Popover, Space, theme, Typography } from "antd";
import type { ReactNode } from "react";

import { useFormat } from "../lib/format";

export interface CheckoutItem {
  label: string;
  value: ReactNode;
  hint?: string;
}

export function CheckoutBar({
  summary,
  items,
  detail,
  balance,
  actions,
}: {
  /** 左侧规格汇总(靛蓝底块) */
  summary?: ReactNode;
  /** 费用项(label 小字在上,value 大号在下) */
  items: CheckoutItem[];
  /** 「费用明细」Popover 内容 */
  detail?: ReactNode;
  /** 余额(未登录不传) */
  balance?: string | null;
  /** 右侧按钮组 */
  actions: ReactNode;
}) {
  const { token } = theme.useToken();
  const { formatMoney } = useFormat();
  return (
    <div
      style={{
        position: "sticky",
        bottom: 0,
        zIndex: 50,
        background: token.colorBgContainer,
        borderTop: `1px solid ${token.colorBorderSecondary}`,
        boxShadow: "0 -4px 12px rgba(0,0,0,0.06)",
        borderRadius: `${token.borderRadiusLG}px ${token.borderRadiusLG}px 0 0`,
        padding: "12px 24px",
        display: "flex",
        alignItems: "center",
        gap: 24,
        flexWrap: "wrap",
      }}
    >
      {summary && (
        <div
          style={{
            background: brand.indigo50,
            color: colorPrimary,
            padding: "8px 14px",
            borderRadius: token.borderRadius,
            fontSize: 13,
            fontWeight: 500,
            maxWidth: 420,
          }}
        >
          {summary}
        </div>
      )}
      <Space size={24} style={{ flex: 1, flexWrap: "wrap" }}>
        {items.map((it) => (
          <div key={it.label}>
            <Typography.Text type="secondary" style={{ fontSize: 12, display: "block" }}>
              {it.label}
              {it.hint ? `(${it.hint})` : ""}
            </Typography.Text>
            <span style={{ fontSize: 20, fontWeight: 700, color: colorPrimary }}>{it.value}</span>
          </div>
        ))}
        {detail && (
          <Popover content={detail} title="费用明细" placement="topLeft">
            <Typography.Link>费用明细</Typography.Link>
          </Popover>
        )}
        {balance !== undefined && (
          <Typography.Text type="secondary">
            余额 <span style={{ fontWeight: 600 }}>{formatMoney(balance)}</span>
          </Typography.Text>
        )}
      </Space>
      <Space size={12}>{actions}</Space>
    </div>
  );
}
