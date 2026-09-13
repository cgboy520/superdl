/** 注意事项聚合条(两端统一,页顶唯一横幅):只放「有时效、可行动」的事;1 条直接显示,多条折成「N 件需要处理」+ 展开列表,严重度取最高(ui-ux-spec §1 规则 1)。
 *  条目来源由页面组装(web:通知 / 到期 / 冻结 / 失败;admin:配置风险 / 取数失败 / 死信)。 */

import { DownOutlined, UpOutlined } from "@ant-design/icons";
import { Alert, Button, Space, Typography } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { fontSize, space } from "../tokens";

export type AttentionSeverity = "info" | "warning" | "error";

export interface AttentionItem {
  key: string;
  severity: AttentionSeverity;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}

const RANK: Record<AttentionSeverity, number> = { info: 0, warning: 1, error: 2 };

export function AttentionBar({ items, style }: { items: AttentionItem[]; style?: React.CSSProperties }) {
  const { t } = useTranslation("shared");
  const [expanded, setExpanded] = useState(false);
  const sorted = [...items].sort((a, b) => RANK[b.severity] - RANK[a.severity]);
  const top = sorted[0];
  if (!top) return null;
  if (sorted.length === 1) {
    return (
      <Alert
        type={top.severity}
        showIcon
        title={top.title}
        description={top.description}
        action={top.action}
        style={style}
      />
    );
  }
  return (
    <Alert
      type={top.severity}
      showIcon
      style={style}
      title={
        <Space size={space.sm} wrap>
          <span>{t("attention.summary", { count: sorted.length })}</span>
          {!expanded && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {sorted
                .map((i) => (typeof i.title === "string" ? i.title : null))
                .filter(Boolean)
                .join(" · ")}
            </Typography.Text>
          )}
        </Space>
      }
      description={
        expanded ? (
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            {sorted.map((i) => (
              <div
                key={i.key}
                style={{ display: "flex", justifyContent: "space-between", gap: space.md, flexWrap: "wrap" }}
              >
                <Space orientation="vertical" size={0}>
                  <span>{i.title}</span>
                  {i.description && (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {i.description}
                    </Typography.Text>
                  )}
                </Space>
                {i.action}
              </div>
            ))}
          </Space>
        ) : undefined
      }
      action={
        <Button
          size="small"
          type="text"
          aria-expanded={expanded}
          icon={expanded ? <UpOutlined /> : <DownOutlined />}
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? t("attention.collapse") : t("attention.expand")}
        </Button>
      }
    />
  );
}
