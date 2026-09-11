/** 固定条数截断表的「只显示最近 N 条」提示;游标分页表不用。 */

import { fontSize } from "@superdl/ui";
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

/** 与后端 app/core/constants.ADMIN_LIST_CAP 对齐,同步改。 */
export const LIST_CAPS = {
  invoices: 200,
  deletions: 200,
  announcements: 200,
} as const;

export function ListCapNote({ rows, cap }: { rows: number; cap: number }) {
  const { t } = useTranslation();
  if (rows < cap) return null;
  return (
    <Typography.Text type="warning" style={{ display: "block", marginTop: 8, fontSize: fontSize.caption }}>
      {t("common.listCapped", { max: cap })}
    </Typography.Text>
  );
}
