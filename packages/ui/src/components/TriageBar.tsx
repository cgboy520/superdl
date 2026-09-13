/** 待处理条(管理端总览置顶):每项 = 严重度图标 + 计数 + 标签,整块是链接(深链到预筛选列表);0 计数弱化不隐藏;查询失败显示「—」。 */

import { Space, Typography, theme } from "antd";
import type { ReactNode } from "react";

import { useThemeColors } from "../hooks/useThemeColors";
import { severityMap, type AlertSeverity } from "../status";
import { fontFamilyMono, fontSize, fontWeight, motion, space } from "../tokens";
import { StatusTag } from "./StatusTag";

export interface TriageItem {
  key: string;
  label: ReactNode;
  /** undefined = 加载中或失败(显示 —) */
  count: number | undefined;
  severity: AlertSeverity;
  /** 计数副注(如「退款 2 · 发票 1」) */
  detail?: ReactNode;
}

export function TriageBar({
  items,
  renderLink,
  ariaLabel,
}: {
  items: TriageItem[];
  /** 用路由 Link 包住整块 */
  renderLink: (item: TriageItem, children: ReactNode) => ReactNode;
  ariaLabel: string;
}) {
  const { token } = theme.useToken();
  const colors = useThemeColors();
  return (
    <div role="navigation" aria-label={ariaLabel} style={{ display: "flex", gap: space.md, flexWrap: "wrap" }}>
      {items.map((it) => {
        const muted = it.count === 0;
        const chip = (
          <div
            className="focus-ring"
            style={{
              display: "flex",
              alignItems: "center",
              gap: space.sm,
              padding: `${space.sm}px ${space.md}px`,
              border: `1px solid ${token.colorBorderSecondary}`,
              borderRadius: token.borderRadiusLG,
              background: token.colorBgContainer,
              color: muted ? token.colorTextSecondary : token.colorText,
              transition: `border-color ${motion.normal}s`,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = colors.primary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = token.colorBorderSecondary;
            }}
          >
            <span style={{ opacity: muted ? 0.6 : 1, display: "inline-flex" }}>
              <StatusTag map={severityMap} value={it.severity} variant="dot" hint={false} />
            </span>
            <span
              style={{
                fontFamily: fontFamilyMono,
                fontSize: fontSize.sectionTitle,
                fontWeight: fontWeight.semibold,
                color: muted ? token.colorTextSecondary : token.colorText,
              }}
            >
              {it.count ?? "—"}
            </span>
            <Space size={4}>
              <span>{it.label}</span>
              {it.detail && (
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {it.detail}
                </Typography.Text>
              )}
            </Space>
          </div>
        );
        return <span key={it.key}>{renderLink(it, chip)}</span>;
      })}
    </div>
  );
}
