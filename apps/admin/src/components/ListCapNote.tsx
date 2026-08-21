/**
 * 列表截断提示。管理端几张表在服务端按固定条数截断(见各 service 的 .limit(...)):
 * 不说出来的话,「只显示最近 N 条」看上去和「一共就这些」一模一样 —— 排查时会据此下错结论。
 */

import { Typography } from "antd";
import { useTranslation } from "react-i18next";

/** 与后端 service 层的硬编码上限一一对应;改后端的同时改这里。 */
export const LIST_CAPS = {
  instances: 200, // orchestrator/service.admin_list_instances
  tenants: 500, // account/service.admin_list_users
  orders: 200, // billing/wallet.admin_list_orders
  adjustments: 200, // adminapi/service.list_adjustments
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
