/** "Only the latest N rows are shown" note for fixed-cap truncated tables; cursor-paginated tables do not use it. */

import { fontSize, space } from "@superdl/ui";
import { Space, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** Fixed list cap. */
export const LIST_CAPS = {
  invoices: 200,
  deletions: 200,
  announcements: 200,
} as const;

export function ListCapNote({ rows, cap, action }: { rows: number; cap: number; action?: ReactNode }) {
  const { t } = useTranslation();
  if (rows < cap) return null;
  return (
    <Space size={space.sm} style={{ marginTop: space.sm }}>
      <Typography.Text type="warning" style={{ fontSize: fontSize.caption }}>
        {t("common.listCapped", { max: cap })}
      </Typography.Text>
      {action}
    </Space>
  );
}
