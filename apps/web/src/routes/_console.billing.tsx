/** 费用中心:余额卡 / 充值 Modal / 消费概览 / 账单与收支明细(服务端 CSV 导出)。Tab 与月份入 URL;充值幂等键按 (amount, channel) 派生。 */

import { POLL } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { Alert, Button, Card, Col, DatePicker, Row, Space, Statistic, Tabs, Typography } from "antd";
import { useMemo, useState } from "react";

import { exportBillingApiV1BillingExportGet } from "@superdl/api-client";
import { addAmounts, amountToScaledNumber, downloadCsvChecked, fontSize, localToday, useCsvExport } from "@superdl/ui";
import { DataErrorAlert, EChart, moneyOr } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { WarnThresholdField } from "../components/WarnThresholdField";
import { useBillSummary, useDailySummary, useMe, usePolicies, useWallet } from "../api/queries";
import { requireAuth } from "../lib/guard";
import { useThemeMode } from "../stores/theme";
import { RechargeModal } from "./-RechargeModal";
import { MonthlyBillsTab } from "./-MonthlyBillsTab";
import { LEDGER_FILTERS, LedgerFilter, LedgerTable } from "./-LedgerTable";
import { RefundTab } from "./-RefundTab";
import { InvoiceTab } from "./-InvoiceTab";

const BILLING_TABS = ["bills", "ledger", "refunds", "invoices"] as const;
export type BillingTab = (typeof BILLING_TABS)[number];

export const Route = createFileRoute("/_console/billing")({
  beforeLoad: requireAuth,
  // Tab/月份/流水类型入 URL;非法值回默认
  validateSearch: (search: Record<string, unknown>): { tab?: BillingTab; month?: string; ledger?: LedgerFilter } => {
    const out: { tab?: BillingTab; month?: string; ledger?: LedgerFilter } = {};
    if (typeof search.tab === "string" && (BILLING_TABS as readonly string[]).includes(search.tab)) {
      out.tab = search.tab as BillingTab;
    }
    if (typeof search.month === "string" && /^\d{4}-\d{2}$/.test(search.month)) {
      out.month = search.month;
    }
    if (typeof search.ledger === "string" && (LEDGER_FILTERS as readonly string[]).includes(search.ledger)) {
      out.ledger = search.ledger as LedgerFilter;
    }
    return out;
  },
  component: BillingPage,
});

function BillingPage() {
  const { formatMoney } = useFormat();
  const { t } = useTranslation();
  const mode = useThemeMode();
  const navigate = useNavigate();
  const { tab, month: monthParam } = Route.useSearch();
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const activeTab: BillingTab = tab ?? "bills";
  const walletQ = useWallet({ refetchInterval: POLL.logs });
  const { data: wallet } = walletQ;
  const { data: me } = useMe();
  const { data: policies } = usePolicies();
  const now = new Date();
  const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const month = monthParam ?? currentMonth;
  const { date, tzOffsetMinutes } = localToday();
  const summaryQ = useBillSummary(month, tzOffsetMinutes);
  const { data: summary } = summaryQ;
  const dailyQ = useDailySummary(date, tzOffsetMinutes);
  const { data: daily } = dailyQ;

  const setSearch = (patch: { tab?: BillingTab; month?: string }) =>
    void navigate({
      to: "/billing",
      search: (prev: { tab?: string; month?: string; ledger?: LedgerFilter }) => ({
        tab: patch.tab ?? (prev.tab as BillingTab | undefined),
        month: patch.month ?? prev.month,
        // 流水类型筛选与 Tab/月份切换共存
        ledger: prev.ledger,
      }),
      replace: true,
    });

  const { doExport: exportCsv, exporting } = useCsvExport(async (tz, lang) => {
    const isBills = activeTab === "bills";
    const csv = (await exportBillingApiV1BillingExportGet(
      isBills
        ? { dataset: "hourly", month, tz_offset_minutes: tz, lang }
        : { dataset: "ledger", tz_offset_minutes: tz, lang },
    )) as string;
    return downloadCsvChecked(isBills ? `superdl-hourly-${month}.csv` : "superdl-ledger.csv", csv);
  });

  const pieData = useMemo(() => {
    const byName = new Map<string, string>();
    for (const i of summary?.items ?? []) {
      const name = i.instance_name ?? t("billing.instanceRef", { id: i.instance_id });
      byName.set(name, addAmounts(byName.get(name) ?? "0", i.total_amount));
    }
    return [...byName.entries()].map(([name, total]) => ({
      name,
      // 万分位整数做图值,与 compareAmounts 同口径
      value: amountToScaledNumber(total),
    }));
  }, [summary, t]);

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("billing.title")}
      </Typography.Title>
      {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
      {(summaryQ.isError || dailyQ.isError) && (
        <DataErrorAlert
          onRetry={() => {
            void summaryQ.refetch();
            void dailyQ.refetch();
          }}
        />
      )}
      {/* /me 未就绪(加载/失败)时不弹实名横幅:已实名用户绝不能被误判成未认证 */}
      {policies?.real_name_required_for_recharge && me != null && me.verification_status !== "verified" && (
        <Alert
          type="warning"
          showIcon
          title={t("billing.realNameRequired")}
          action={
            <Link to="/settings">
              <Button size="small">{t("billing.goVerify")}</Button>
            </Link>
          }
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card>
            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
              <Statistic
                title={t("billing.availableBalance")}
                value={moneyOr(formatMoney(wallet?.balance ?? "0.00"), wallet != null)}
                styles={{ content: { fontSize: fontSize.kpi } }}
              />
              <Button type="primary" size="large" onClick={() => setRechargeOpen(true)}>
                {t("billing.recharge")}
              </Button>
            </Space>
            <WarnThresholdField size="small" style={{ marginTop: 12 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title={t("billing.monthSpend", { month })}
            extra={
              <DatePicker
                picker="month"
                size="small"
                allowClear={false}
                aria-label={t("billing.monthPicker")}
                value={dayjs(`${month}-01`)}
                disabledDate={(d) => d.isAfter(dayjs(), "month")}
                onChange={(d) => {
                  if (d) setSearch({ month: d.format("YYYY-MM") });
                }}
              />
            }
          >
            <Row>
              <Col xs={24} md={10}>
                <Statistic
                  title={t("billing.gpuTotal")}
                  value={moneyOr(formatMoney(summary?.gpu_total ?? "0.00"), summary != null)}
                />
                <Statistic
                  title={t("billing.diskTotal")}
                  value={moneyOr(formatMoney(summary?.disk_total ?? "0.00"), summary != null)}
                  styles={{ content: { fontSize: fontSize.sectionTitle } }}
                />
                <Statistic
                  title={t("instances.labelToday")}
                  value={moneyOr(
                    formatMoney(daily ? addAmounts(daily.gpu_total, daily.disk_total) : "0.00"),
                    daily != null,
                  )}
                  styles={{ content: { fontSize: fontSize.sectionTitle } }}
                />
              </Col>
              <Col xs={24} md={14}>
                <EChart
                  theme={mode === "dark" ? "web-dark" : "web-light"}
                  style={{ height: 160 }}
                  ariaLabel={t("billing.monthSpend", { month })}
                  empty={pieData.length === 0 ? t("billing.noSpendThisMonth") : false}
                  option={{
                    tooltip: { trigger: "item" },
                    series: [
                      {
                        type: "pie",
                        radius: ["45%", "70%"],
                        data: pieData,
                        label: { fontSize: fontSize.caption },
                      },
                    ],
                  }}
                />
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>

      {/* 导出按钮必须放 Card 的 extra:tabBarExtraContent 会把 button 放进 role="tablist"(axe aria-required-children) */}
      <Card
        extra={
          // CSV 导出仅覆盖账单/流水两个 Tab
          activeTab === "bills" || activeTab === "ledger" ? (
            <Button size="small" loading={exporting} onClick={() => void exportCsv()}>
              {t("billing.exportCsv")}
            </Button>
          ) : undefined
        }
      >
        <Tabs
          activeKey={activeTab}
          onChange={(k) => setSearch({ tab: k as BillingTab })}
          items={[
            {
              key: "bills",
              label: t("billing.tabBills"),
              children: <MonthlyBillsTab month={month} tzOffsetMinutes={tzOffsetMinutes} />,
            },
            { key: "ledger", label: t("billing.tabLedger"), children: <LedgerTable /> },
            { key: "refunds", label: t("billing.tabRefunds"), children: <RefundTab /> },
            { key: "invoices", label: t("billing.tabInvoices"), children: <InvoiceTab /> },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("copy.dailyCostNote")}
          {" · "}
          {t("copy.billingDayBoundary")}
        </Typography.Text>
      </Card>
      <RechargeModal open={rechargeOpen} onClose={() => setRechargeOpen(false)} />
    </Space>
  );
}
