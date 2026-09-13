/** 充值订单 Tab:FilterBar(状态 / 订单号 / 日期,入 URL)+ 行内核验 / 补单 + 导出。 */

import { useQueryClient } from "@tanstack/react-query";
import { Button, DatePicker, Input, Select } from "antd";
import dayjs from "dayjs";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { controlWidth, flattenPages, layout, orderStatusMap } from "@superdl/ui";
import { CursorTable, EmptyState, FilterBar } from "@superdl/ui/components";
import { useCsvExport, useUrlCommittedInput, useUrlFilters } from "@superdl/ui";

import { type OrderRow, exportOrdersCsv, useOrders } from "../../api";
import { useOrderActions } from "../../components/orderActions";
import { useOrderColumns } from "../../components/orderColumns";
import { useFinanceFilters } from "./-financeFilters";

export function OrdersTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const qc = useQueryClient();
  // 补单入账后订单状态变,整个 orders 前缀失效
  const actions = useOrderActions(() => void qc.invalidateQueries({ queryKey: ["admin", "orders"] }));
  const orderColumns = useOrderColumns({ withTenant: true, actions });
  // 筛选条件入 URL(status/订单号/下单日)
  const { search, setFilters } = useFinanceFilters();
  const status = search.o_status;
  const orderNo = search.o_no;
  const filters = useUrlFilters({
    search: { o_status: status, o_no: orderNo, o_day: search.o_day },
    keys: ["o_status", "o_no", "o_day"],
    commit: setFilters,
  });
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
  const total = q.data?.pages[0]?.total ?? undefined;
  const { doExport, exporting } = useCsvExport((tz, lang) => exportOrdersCsv(params, tz, lang));
  return (
    <>
      <FilterBar
        hasFilter={filters.hasFilter}
        onClear={filters.clear}
        count={total}
        extra={
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        }
      >
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: controlWidth.sm }}
          value={status}
          onChange={(v) => setFilters({ o_status: v })}
          options={Object.entries(orderStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
        />
        <Input.Search
          allowClear
          placeholder={t("finance.searchOrderPlaceholder")}
          style={{ width: controlWidth.md }}
          value={orderNoInput}
          onChange={(e) => setOrderNoInput(e.target.value)}
          onSearch={(v) => commitOrderNo(v.trim() || undefined)}
        />
        <DatePicker
          value={day}
          onChange={(d) => setFilters({ o_day: d ? d.format("YYYY-MM-DD") : undefined })}
          allowClear
        />
      </FilterBar>
      <CursorTable<OrderRow>
        query={q}
        rows={orders}
        emptyNode={
          <EmptyState
            scene={filters.hasFilter ? "search" : "list"}
            compact
            secondaryAction={
              filters.hasFilter ? (
                <Button size="small" onClick={filters.clear}>
                  {t("filter.clear", { ns: "shared" })}
                </Button>
              ) : undefined
            }
          />
        }
        scroll={{ x: 1100 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="order_no"
        columns={orderColumns}
      />
      {actions.modals}
    </>
  );
}
