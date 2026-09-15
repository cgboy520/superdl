/** 充值订单列(两处共用);withTenant 决定是否带「租户」列,actions 决定是否带行内动作列(核验 + 更多 ▾ 补单)。 */

import { formatDateTime, metaOf, orderStatusMap, paymentChannelMap } from "@superdl/ui";
import { GatedButton, Mono, RowActions } from "@superdl/ui/components";
import type { TableColumnsType } from "antd";
import { useTranslation } from "react-i18next";

import type { OrderRow } from "../api";
import { useFormat } from "@superdl/ui";
import { StatusTag } from "@superdl/ui/components";
import type { OrderActions } from "./orderActions";
import { tenantColumn } from "./TenantLink";

export function useOrderColumns({
  withTenant,
  actions,
}: {
  withTenant: boolean;
  /** 传入即出操作列(固定右);缺省 = 只读列表 */
  actions?: OrderActions;
}): TableColumnsType<OrderRow> {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  return [
    {
      title: t("finance.colOrderNo"),
      dataIndex: "order_no",
      ...(actions ? { fixed: "left" as const, width: 200 } : {}),
      render: (v: string) => <Mono>{v}</Mono>,
    },
    ...(withTenant ? [tenantColumn<OrderRow>(t("finance.colTenant"))] : []),
    { title: t("finance.colAmount"), dataIndex: "amount", align: "right", render: (v: string) => formatMoney(v) },
    {
      title: t("finance.colChannel"),
      dataIndex: "channel",
      render: (v: string) => {
        const m = metaOf(paymentChannelMap, v);
        return m ? t(m.labelKey) : v;
      },
    },
    {
      title: t("finance.colStatus"),
      dataIndex: "status",
      render: (v: string) => {
        return <StatusTag map={orderStatusMap} value={v} />;
      },
    },
    { title: t("finance.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
    ...(actions
      ? [
          {
            title: t("finance.colAction"),
            key: "actions",
            fixed: "right" as const,
            width: 150,
            render: (_: unknown, r: OrderRow) => (
              <RowActions
                primary={
                  <GatedButton
                    size="small"
                    reason={actions.writable ? undefined : t("finance.financeOnlyVerify")}
                    onClick={() => actions.verify(r.order_no)}
                  >
                    {t("finance.verify")}
                  </GatedButton>
                }
                more={[
                  {
                    key: "backfill",
                    label: t("finance.backfill"),
                    reason: !actions.writable
                      ? t("finance.financeOnlyBackfill")
                      : r.status === "paid"
                        ? t("finance.backfillNotApplicable")
                        : undefined,
                    onClick: () => actions.openBackfill(r.order_no),
                  },
                ]}
              />
            ),
          },
        ]
      : []),
  ];
}
