/** 充值订单 Tab:订单号 / 日期筛选、核验与补单。 */

import { Button, DatePicker, Input, Select, Space } from "antd";
import dayjs from "dayjs";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { flattenPages, orderStatusMap } from "@superdl/ui";
import { CursorTable } from "@superdl/ui/components";
import { useCsvExport, useUrlCommittedInput } from "@superdl/ui";

import { type OrderRow, exportOrdersCsv, useOrders } from "../../api";
import { useOrderColumns } from "../../components/orderColumns";
import { useFinanceFilters } from "./-financeFilters";

export function OrdersTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const orderColumns = useOrderColumns({ withTenant: true });
  // 筛选条件入 URL(status/订单号/下单日)
  const { search, setFilters } = useFinanceFilters();
  const status = search.o_status;
  const orderNo = search.o_no;
  // 检索防抖回写 URL;URL 回流同步进输入框
  const commitOrderNo = useCallback((next: string | undefined) => setFilters({ o_no: next }), [setFilters]);
  const { value: orderNoInput, setValue: setOrderNoInput } = useUrlCommittedInput(orderNo, commitOrderNo);
  const day = search.o_day ? dayjs(search.o_day) : null;
  const params = {
    ...(status ? { status } : {}),
    ...(orderNo ? { order_no: orderNo } : {}),
    ...(search.o_day ? { day: search.o_day } : {}),
  };
  const q = useOrders(params);
  const orders = flattenPages(q.data);
  const { doExport, exporting } = useCsvExport((tz, lang) => exportOrdersCsv(params, tz, lang));
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: 160 }}
          value={status}
          onChange={(v) => setFilters({ o_status: v })}
          options={Object.entries(orderStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
        />
        <Input.Search
          allowClear
          placeholder={t("finance.searchOrderPlaceholder")}
          style={{ width: 260 }}
          value={orderNoInput}
          onChange={(e) => setOrderNoInput(e.target.value)}
          onSearch={(v) => commitOrderNo(v.trim() || undefined)}
        />
        <DatePicker
          value={day}
          onChange={(d) => setFilters({ o_day: d ? d.format("YYYY-MM-DD") : undefined })}
          allowClear
        />
        <Button onClick={() => void doExport()} loading={exporting}>
          {t("common.exportCsv")}
        </Button>
      </Space>
      <CursorTable<OrderRow> query={q} rows={orders} scroll={{ x: 900 }} rowKey="order_no" columns={orderColumns} />
    </>
  );
}
