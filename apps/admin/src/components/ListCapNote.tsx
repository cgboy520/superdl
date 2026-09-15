/** 固定条数截断表的「只显示最近 N 条」提示;游标分页表不用。 */

import { fontSize, space } from "@superdl/ui";
import { Space, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** 固定列表截断条数。 */
export const LIST_CAPS = {
  invoices: 200,
  deletions: 200,
  announcements: 200,
  alerts: 50,
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
