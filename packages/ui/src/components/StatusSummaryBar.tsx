/** Controlled status switch bar, each item with an optional count and attention dot. */

import { Segmented, Space, Typography } from "antd";
import type { ReactNode } from "react";

import { useThemeColors } from "../hooks/useThemeColors";
import { fontSize, space } from "../tokens";

export interface StatusSummaryItem {
  key: string;
  label: ReactNode;
  count?: number;
  /** Attention dot (needs handling) */
  tone?: "default" | "warning" | "error";
}

export function StatusSummaryBar({
  items,
  value,
  onChange,
  ariaLabel,
}: {
  items: StatusSummaryItem[];
  value: string;
  onChange: (key: string) => void;
  ariaLabel: string;
}) {
  const colors = useThemeColors();
  return (
    <Segmented
      aria-label={ariaLabel}
      value={value}
      onChange={onChange}
      options={items.map((it) => ({
        value: it.key,
        label: (
          <Space size={space.xs} align="center">
            {it.tone && it.tone !== "default" && (
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 3,
                  background: it.tone === "error" ? colors.negative : colors.warning,
                  display: "inline-block",
                }}
              />
            )}
            <span>{it.label}</span>
            {it.count !== undefined && (
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {it.count}
              </Typography.Text>
            )}
          </Space>
        ),
      }))}
    />
  );
}
