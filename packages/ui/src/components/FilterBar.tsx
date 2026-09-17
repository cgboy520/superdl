/** Filter bar (shared by both consoles): filter controls + "Clear filters" on the left, "N in total" + extra actions on the right; the filter state belongs to the page (in the URL), this only lays out and clears. */

import { Button, Space, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { fontSize, space } from "../tokens";

export function FilterBar({
  children,
  count,
  hasFilter,
  onClear,
  extra,
}: {
  children: ReactNode;
  /** Current result count (omit when unknown) */
  count?: number;
  hasFilter: boolean;
  onClear: () => void;
  /** Extra actions on the right (refresh / export) */
  extra?: ReactNode;
}) {
  const { t } = useTranslation("shared");
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: space.md,
        flexWrap: "wrap",
        marginBottom: space.md,
      }}
    >
      <Space size={space.sm} wrap>
        {children}
        {hasFilter && (
          <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={onClear}>
            {t("filter.clear")}
          </Button>
        )}
      </Space>
      <Space size={space.sm} wrap>
        {count != null && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("filter.count", { count })}
          </Typography.Text>
        )}
        {extra}
      </Space>
    </div>
  );
}
