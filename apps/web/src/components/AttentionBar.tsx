/** 注意事项聚合条(实例列表页顶部唯一的常驻横幅):公告 / 余额预警 / 欠费 来自未读通知;页面再追加到期 / 冻结 / 失败等条目。
 *  规则(ui-ux-spec §1):只放「有时效、可行动」的事;1 条直接显示,多条折成「N 件需要处理」+ 展开列表;严重度取最高。 */

import { DownOutlined, UpOutlined } from "@ant-design/icons";
import { fontSize, space } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Alert, Button, Space, Typography } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useNotifications } from "../api/queries";

export type AttentionSeverity = "info" | "warning" | "error";

export interface AttentionItem {
  key: string;
  severity: AttentionSeverity;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}

const RANK: Record<AttentionSeverity, number> = { info: 0, warning: 1, error: 2 };

/** 从未读通知派生的全站条目(公告 / 余额预警 / 欠费)。 */
export function useNotificationAttention(): AttentionItem[] {
  const { t } = useTranslation();
  const { data: unread } = useNotifications({ unread: true });
  const items: AttentionItem[] = [];
  const list = unread?.items ?? [];
  const announcement = list.find((n) => n.type === "announcement");
  if (announcement) {
    items.push({
      key: `announcement:${announcement.id}`,
      severity: "info",
      title: t("attention.announcement", { title: announcement.title }),
      description: announcement.content,
      action: (
        <Link to="/notifications">
          <Button size="small">{t("attention.viewNotifications")}</Button>
        </Link>
      ),
    });
  }
  if (list.some((n) => n.type === "arrears")) {
    items.push({
      key: "arrears",
      severity: "error",
      title: t("attention.arrears"),
      action: (
        <Link to="/billing">
          <Button size="small" type="primary" danger>
            {t("attention.goRecharge")}
          </Button>
        </Link>
      ),
    });
  } else if (list.some((n) => n.type === "balance_warn")) {
    items.push({
      key: "balance_warn",
      severity: "warning",
      title: t("attention.balanceWarn"),
      action: (
        <Link to="/billing">
          <Button size="small" type="primary">
            {t("attention.goRecharge")}
          </Button>
        </Link>
      ),
    });
  }
  return items;
}

export function AttentionBar({ items }: { items: AttentionItem[] }) {
  const { t } = useTranslation();
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
      />
    );
  }
  return (
    <Alert
      type={top.severity}
      showIcon
      title={
        <Space size={space.sm} wrap>
          <span>{t("attention.summary", { count: sorted.length })}</span>
          {!expanded && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {sorted.map((i) => (typeof i.title === "string" ? i.title : null)).filter(Boolean).join(" · ")}
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
