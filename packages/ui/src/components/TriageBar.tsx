/** Triage bar (top of the admin overview): each item = severity icon + count + label, the whole block links to a pre-filtered list; zero counts are muted, not hidden; failed queries show "—". */

import { Space, Typography, theme } from "antd";
import type { ReactNode } from "react";

import { useThemeColors } from "../hooks/useThemeColors";
import { severityMap, type AlertSeverity } from "../status";
import { fontFamilyMono, fontSize, fontWeight, motion, space } from "../tokens";
import { StatusTag } from "./StatusTag";

export interface TriageItem {
  key: string;
  label: ReactNode;
  /** undefined = loading or failed (shows —) */
  count: number | undefined;
  severity: AlertSeverity;
  /** Count footnote (e.g. "refunds 2 · invoices 1") */
  detail?: ReactNode;
}

export function TriageBar({
  items,
  renderLink,
  ariaLabel,
}: {
  items: TriageItem[];
  /** Wrap the whole block in a router Link */
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
            <Space size={space.xs}>
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
