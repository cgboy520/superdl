/** 固定条数截断表的「只显示最近 N 条」提示;游标分页表不用。 */

import { fontSize } from "@superdl/ui";
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

/** 与后端 service 层硬编码上限一一对应,同步改。 */
export const LIST_CAPS = {
  invoices: 200, // billing/invoices.admin_list_invoices
  deletions: 200, // account/service.ADMIN_DELETION_LIST_CAP
  announcements: 200, // notify/service.ANNOUNCEMENT_LIST_CAP
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
