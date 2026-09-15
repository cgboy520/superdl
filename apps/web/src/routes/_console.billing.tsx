/** 费用中心:余额与充值、月度概览、账单、收支明细及 CSV 导出;Tab 与月份写入 URL。 */

import { POLL, useAutoRefresh, useThemeColors } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { Button, Card, Col, DatePicker, Row, Space, Tabs, Typography } from "antd";
import { useMemo, useState } from "react";

import { exportBillingApiV1BillingExportGet } from "@superdl/api-client";
import {
  addAmounts,
  amountToScaledNumber,
  compareAmounts,
  downloadCsvChecked,
  fontSize,
  fontWeight,
  localToday,
  space,
  useCsvExport,
} from "@superdl/ui";
import { AttentionBar, KpiGrid, moneyOr, PageContainer, StatCard, type AttentionItem } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { useBillSummary, useDailySummary, useMe, usePolicies, useWallet } from "../api/queries";
import { requireAuth } from "../lib/guard";
import { RechargeModal } from "./-RechargeModal";
import { MonthlyBillsTab } from "./-MonthlyBillsTab";
import { LEDGER_FILTERS, LedgerFilter, LedgerTable } from "./-LedgerTable";
import { RefundTab } from "./-RefundTab";
import { InvoiceTab } from "./-InvoiceTab";

const BILLING_TABS = ["bills", "ledger", "refunds", "invoices"] as const;
export type BillingTab = (typeof BILLING_TABS)[number];

/** 「按实例」最多列几行,其余折成一行说明 */
const BY_INSTANCE_ROWS = 8;

export const Route = createFileRoute("/_console/billing")({
  beforeLoad: requireAuth,
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

/** 按实例消费横条:条长按最大值归一,金额右对齐。 */
function ByInstanceBar({ name, amount, ratio }: { name: string; amount: string; ratio: number }) {
  const colors = useThemeColors();
  const { formatMoney } = useFormat();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: space.sm }}>
      <Typography.Text ellipsis style={{ width: 140, flex: "none", fontSize: fontSize.caption }}>
        {name}
      </Typography.Text>
      <div style={{ flex: 1, minWidth: 0, height: 8, borderRadius: 4, background: colors.primarySoft }}>
        <div
          style={{
            width: `${Math.max(ratio * 100, 2)}%`,
            height: "100%",
            borderRadius: 4,
            background: colors.primary,
          }}
        />
      </div>
      <Typography.Text style={{ flex: "none", fontSize: fontSize.caption }}>{formatMoney(amount)}</Typography.Text>
    </div>
  );
}

function BillingPage() {
  const { formatMoney } = useFormat();
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const { tab, month: monthParam } = Route.useSearch();
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const activeTab: BillingTab = tab ?? "bills";
  const auto = useAutoRefresh(POLL.logs);
  const walletQ = useWallet({ refetchInterval: auto.refetchInterval });
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

  const byInstance = useMemo(() => {
    const byName = new Map<string, string>();
    for (const i of summary?.items ?? []) {
      const name = i.instance_name ?? t("billing.instanceRef", { id: i.instance_id });
      byName.set(name, addAmounts(byName.get(name) ?? "0", i.total_amount));
    }
    return [...byName.entries()]
      .map(([name, amount]) => ({ name, amount }))
      .sort((a, b) => compareAmounts(b.amount, a.amount));
  }, [summary, t]);
  const maxAmount = amountToScaledNumber(byInstance[0]?.amount ?? "0");

  const attention: AttentionItem[] = [];
  if (walletQ.isError) {
    attention.push({
      key: "wallet",
      severity: "error",
      title: t("billing.walletFailed"),
      description: t("shared:query.partialFailedDesc"),
      action: (
        <Button size="small" onClick={() => void walletQ.refetch()}>
          {t("shared:common.retry")}
        </Button>
      ),
    });
  }
  if (summaryQ.isError || dailyQ.isError) {
    attention.push({
      key: "summary",
      severity: "error",
      title: t("billing.summaryFailed"),
      description: t("shared:query.partialFailedDesc"),
      action: (
        <Button
          size="small"
          onClick={() => {
            void summaryQ.refetch();
            void dailyQ.refetch();
          }}
        >
          {t("shared:common.retry")}
        </Button>
      ),
    });
  }
  if (policies?.real_name_required_for_recharge && me != null && me.verification_status !== "verified") {
    attention.push({
      key: "realName",
      severity: "warning",
      title: t("billing.realNameRequired"),
      action: (
        <Link to="/settings">
          <Button size="small">{t("billing.goVerify")}</Button>
        </Link>
      ),
    });
  }

  return (
    <PageContainer
      title={t("billing.title")}
      freshness={{
        updatedAt: walletQ.dataUpdatedAt,
        intervalMs: auto.intervalMs,
        paused: auto.paused,
        onTogglePause: auto.toggle,
        onRefresh: () => void walletQ.refetch(),
        refreshing: walletQ.isRefetching,
      }}
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        {attention.length > 0 && <AttentionBar items={attention} />}
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: space.md,
                  flexWrap: "wrap",
                }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: space.xs, minWidth: 0 }}>
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.body }}>
                    {t("billing.availableBalance")}
                  </Typography.Text>
                  <div style={{ fontSize: fontSize.kpi, fontWeight: fontWeight.semibold, lineHeight: 1.3 }}>
                    {moneyOr(formatMoney(wallet?.balance ?? "0.00"), wallet != null)}
                  </div>
                </div>
                <Button type="primary" onClick={() => setRechargeOpen(true)}>
                  {t("billing.recharge")}
                </Button>
              </div>
              {me?.low_balance_warn_hours != null && (
                <Typography.Text
                  type="secondary"
                  style={{ display: "block", marginTop: space.sm, fontSize: fontSize.caption }}
                >
                  {t("billing.thresholdLine", { hours: me.low_balance_warn_hours })}
                  {" · "}
                  <Link to="/settings" hash="notify">
                    {t("billing.thresholdEdit")}
                  </Link>
                </Typography.Text>
              )}
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
              <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
                <KpiGrid
                  minWidth={120}
                  items={[
                    <StatCard
                      key="gpu"
                      valueSize="sectionTitle"
                      title={t("billing.gpuTotal")}
                      value={moneyOr(formatMoney(summary?.gpu_total ?? "0.00"), summary != null)}
                    />,
                    <StatCard
                      key="disk"
                      valueSize="sectionTitle"
                      title={t("billing.diskTotal")}
                      value={moneyOr(formatMoney(summary?.disk_total ?? "0.00"), summary != null)}
                    />,
                    <StatCard
                      key="today"
                      valueSize="sectionTitle"
                      title={t("instances.labelToday")}
                      value={moneyOr(
                        formatMoney(daily ? addAmounts(daily.gpu_total, daily.disk_total) : "0.00"),
                        daily != null,
                      )}
                    />,
                  ]}
                />
                <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("billing.byInstance")}
                  </Typography.Text>
                  {byInstance.length === 0 ? (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {t("billing.noSpendThisMonth")}
                    </Typography.Text>
                  ) : (
                    <>
                      {byInstance.slice(0, BY_INSTANCE_ROWS).map((row) => (
                        <ByInstanceBar
                          key={row.name}
                          name={row.name}
                          amount={row.amount}
                          ratio={maxAmount > 0 ? amountToScaledNumber(row.amount) / maxAmount : 0}
                        />
                      ))}
                      {byInstance.length > BY_INSTANCE_ROWS && (
                        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                          {t("billing.otherInstances", { count: byInstance.length - BY_INSTANCE_ROWS })}
                        </Typography.Text>
                      )}
                    </>
                  )}
                </Space>
              </Space>
            </Card>
          </Col>
        </Row>

        <Card>
          <div style={{ display: "flex", alignItems: "flex-start", gap: space.md }}>
            <Tabs
              style={{ flex: 1, minWidth: 0 }}
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
            {(activeTab === "bills" || activeTab === "ledger") && (
              <div style={{ paddingTop: space.md }}>
                <Button size="small" loading={exporting} onClick={() => void exportCsv()}>
                  {t("billing.exportCsv")}
                </Button>
              </div>
            )}
          </div>
          {activeTab === "bills" && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("copy.dailyCostNote")}
              {" · "}
              {t("copy.billingDayBoundary")}
            </Typography.Text>
          )}
        </Card>
        <RechargeModal open={rechargeOpen} onClose={() => setRechargeOpen(false)} />
      </Space>
    </PageContainer>
  );
}
