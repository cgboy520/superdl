/** 列表截断提示:管理端仍按固定条数截断的表,页面须显式说明「只显示最近 N 条」。
 * 订单/调账/退款/租户/实例/审计已改游标分页(P1-13),不再出现在这里。 */

import { Typography } from "antd";
import { useTranslation } from "react-i18next";

/** 与后端 service 层的硬编码上限一一对应;改后端的同时改这里。 */
export const LIST_CAPS = {
  invoices: 200, // billing/invoices.admin_list_invoices
  tickets: 200, // tickets/service.admin_list_tickets
  deletions: 200, // account/service.ADMIN_DELETION_LIST_CAP
  announcements: 200, // notify/service.ANNOUNCEMENT_LIST_CAP
} as const;

export function ListCapNote({ rows, cap }: { rows: number; cap: number }) {
  const { t } = useTranslation();
  if (rows < cap) return null;
  return (
    <Typography.Text type="warning" style={{ display: "block", marginTop: 8, fontSize: 12 }}>
      {t("common.listCapped", { max: cap })}
    </Typography.Text>
  );
}
