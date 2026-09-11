/** 充值订单 Tab:订单号 / 日期筛选、核验与补单。 */

import { Button, DatePicker, Input, Select, Space, Table } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { orderStatusMap } from "@superdl/ui";
import { LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useCsvExport } from "@superdl/ui";

import { type OrderRow, exportOrdersCsv, isApiError, useOrders } from "../../api";
import { useOrderColumns } from "../../components/orderColumns";
import { useFinanceFilters } from "./-financeFilters";

export function OrdersTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const orderColumns = useOrderColumns({ withTenant: true });
  // 筛选条件入 URL(status/订单号/下单日)
  const { search, setFilters } = useFinanceFilters();
  const status = search.o_status;
  const orderNo = search.o_no ?? "";
  // 检索 commit 制:回车/点搜索/清空才写 URL;URL 回流走渲染期派生态
  const [orderNoInput, setOrderNoInput] = useState(orderNo);
  const [prevOrderNo, setPrevOrderNo] = useState(orderNo);
  if (orderNo !== prevOrderNo) {
    setPrevOrderNo(orderNo);
    setOrderNoInput(orderNo);
  }
  const day = search.o_day ? dayjs(search.o_day) : null;
  const params = {
    ...(status ? { status } : {}),
    ...(orderNo ? { order_no: orderNo } : {}),
    ...(search.o_day ? { day: search.o_day } : {}),
  };
  const q = useOrders(params);
  const orders: OrderRow[] = q.data?.pages.flatMap((p) => p.items) ?? [];
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
        onSearch={(v) => setFilters({ o_no: v.trim() || undefined })}
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
      <Table<OrderRow>
        scroll={{ x: 900 }}
        rowKey="order_no"
        dataSource={orders}
        loading={q.isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={q.isError}
              isForbidden={isApiError(q.error) && q.error.status === 403}
              onRetry={() => void q.refetch()}
            />
          ),
        }}
        columns={orderColumns}
      />
      <LoadMore
        hasNextPage={Boolean(q.hasNextPage)}
        loading={q.isFetchingNextPage}
        isError={q.isFetchNextPageError}
        loadedCount={orders.length}
        onLoadMore={() => void q.fetchNextPage()}
      />
    </>
  );
}

