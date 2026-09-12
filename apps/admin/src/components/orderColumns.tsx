/** 充值订单列(两处共用);withTenant 决定是否带「租户」列。 */

import { formatDateTime, metaOf, orderStatusMap, paymentChannelMap } from "@superdl/ui";
import type { TableColumnsType } from "antd";
import { useTranslation } from "react-i18next";

import type { OrderRow } from "../api";
import { useFormat } from "@superdl/ui";
import { StatusTag } from "./StatusTag";
import { tenantColumn } from "./TenantLink";

export function useOrderColumns({ withTenant }: { withTenant: boolean }): TableColumnsType<OrderRow> {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  return [
    { title: t("finance.colOrderNo"), dataIndex: "order_no" },
    ...(withTenant ? [tenantColumn<OrderRow>(t("finance.colTenant"))] : []),
    { title: t("finance.colAmount"), dataIndex: "amount", render: (v: string) => formatMoney(v) },
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
  ];
}
