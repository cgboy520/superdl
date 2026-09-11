/** 财务对账页:对账卡 + Tab(充值订单 / 退款 / 发票 / 调账 / 结算缺口 / 支付异常 / 审计),各 Tab 拆在同目录 -Xxx.tsx;筛选态入 URL(-financeFilters)。 */

import { WarningOutlined } from "@ant-design/icons";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, Card, Space, Tabs, Tag, Tooltip } from "antd";
import { useTranslation } from "react-i18next";

import { adjustmentStatusMap, adminColors, invoiceStatusMap, orderStatusMap, payoutChannelMap, refundStatusMap } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";

import { useAnomalies } from "../../api";
import { AuditTable } from "../../components/AuditTable";
import { canReadInvoices, useAdminRole } from "../../stores/auth";
import { SettlementGapsTab } from "./-SettlementGapsTab";
import { DAY_RE, FINANCE_TABS, FinanceSearch, FinanceTab, PERIOD_RE } from "./-financeFilters";
import { ReconciliationCard } from "./-ReconciliationCard";
import { OrdersTab } from "./-OrdersTab";
import { AdjustmentsTab } from "./-AdjustmentsTab";
import { AnomaliesTab } from "./-AnomaliesTab";
import { RefundsTab } from "./-RefundsTab";
import { InvoicesTab } from "./-InvoicesTab";

export const Route = createFileRoute("/_app/finance")({
  // 筛选条件入 URL,非法值剥离
  validateSearch: (search: Record<string, unknown>): FinanceSearch => ({
    tab: FINANCE_TABS.includes(search.tab as FinanceTab) ? (search.tab as FinanceTab) : undefined,
    o_status:
      typeof search.o_status === "string" && search.o_status in orderStatusMap
        ? search.o_status
        : undefined,
    o_no: typeof search.o_no === "string" && search.o_no ? search.o_no : undefined,
    o_day:
      typeof search.o_day === "string" && DAY_RE.test(search.o_day) ? search.o_day : undefined,
    r_status:
      typeof search.r_status === "string" && search.r_status in refundStatusMap
        ? search.r_status
        : undefined,
    r_day:
      typeof search.r_day === "string" && DAY_RE.test(search.r_day) ? search.r_day : undefined,
    r_channel:
      typeof search.r_channel === "string" && search.r_channel in payoutChannelMap
        ? search.r_channel
        : undefined,
    i_status:
      typeof search.i_status === "string" && search.i_status in invoiceStatusMap
        ? search.i_status
        : undefined,
    i_period:
      typeof search.i_period === "string" && PERIOD_RE.test(search.i_period)
        ? search.i_period
        : undefined,
    a_status:
      typeof search.a_status === "string" && search.a_status in adjustmentStatusMap
        ? search.a_status
        : undefined,
    a_day:
      typeof search.a_day === "string" && DAY_RE.test(search.a_day) ? search.a_day : undefined,
    a_uid:
      typeof search.a_uid === "number" && Number.isInteger(search.a_uid) && search.a_uid > 0
        ? search.a_uid
        : typeof search.a_uid === "string" && /^\d+$/.test(search.a_uid)
          ? Number(search.a_uid)
          : undefined,
  }),
  component: FinancePage,
});

function FinancePage() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate();
  const tab = Route.useSearch({ select: (s) => s.tab });
  const role = useAdminRole();
  const anomaliesQ = useAnomalies();
  const { data: anomalies, isError: anomaliesError } = anomaliesQ;
  const anomalyCount = anomalies?.length ?? 0;
  // 发票 Tab 只给 finance/admin;无权直达 ?tab=invoices 回落订单 Tab 并明示原因
  const showInvoices = canReadInvoices(role);
  const activeTab = tab ?? "orders";
  const invoicesDenied = activeTab === "invoices" && !showInvoices;
  return (
    <PageContainer width="full" title={t("menu.finance")}>
      <ReconciliationCard />
      <Card style={{ marginTop: 16 }}>
        {invoicesDenied && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            title={t("finance.tabInvoicesDenied")}
          />
        )}
        <Tabs
          activeKey={invoicesDenied ? "orders" : activeTab}
          onChange={(key) =>
            void navigate({
              to: "/finance",
              replace: true,
              search: key === "orders" ? {} : { tab: key as FinanceTab },
            })
          }
          items={[
            { key: "orders", label: t("finance.tabOrders"), children: <OrdersTab /> },
            { key: "refunds", label: t("finance.tabRefunds"), children: <RefundsTab /> },
            ...(showInvoices
              ? [{ key: "invoices", label: t("finance.tabInvoices"), children: <InvoicesTab /> }]
              : []),
            { key: "adjustments", label: t("finance.tabAdjustments"), children: <AdjustmentsTab /> },
            { key: "gaps", label: t("finance.tabSettlementGaps"), children: <SettlementGapsTab /> },
            {
              key: "anomalies",
              label: (
                <Space size={6}>
                  {t("finance.tabAnomalies")}
                  {/* 计数查询失败显示警示图标,不静默为 0 */}
                  {anomaliesError ? (
                    <Tooltip title={t("common.loadFailed", { ns: "shared" })}>
                      <WarningOutlined style={{ color: adminColors.alertAccent }} />
                    </Tooltip>
                  ) : (
                    anomalyCount > 0 && <Tag color="red">{anomalyCount}</Tag>
                  )}
                </Space>
              ),
              children: <AnomaliesTab />,
            },
            { key: "audit", label: t("menu.audit"), children: <AuditTable /> },
          ]}
        />
      </Card>
    </PageContainer>
  );
}

