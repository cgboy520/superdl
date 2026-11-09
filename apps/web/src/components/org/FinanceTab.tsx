/** Members' spending at a glance (owners and admins, every read audited): available balance, this billing month's bills since joining, hourly burn and the hours it lasts; below it, the balance moves between admins and members. */

import type { BalanceTransferOut, MemberFinanceOut } from "@superdl/api-client";
import { formatDateTime, space, useFormat } from "@superdl/ui";
import { KpiGrid, StatCard, TableErrorEmpty } from "@superdl/ui/components";
import { Card, Space, Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useOrgFinance, useOrgTransfers } from "../../api/queries";

/** Allocations to and reclaims from members, newest first (spend-only money; the ledger of each side records the move). */
function TransfersCard() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const q = useOrgTransfers();
  return (
    <Card size="small" title={t("org.balance.historyTitle")}>
      {q.isError ? (
        <TableErrorEmpty isError compact onRetry={() => void q.refetch()} />
      ) : (
        <Table<BalanceTransferOut>
          rowKey="uuid"
          size="small"
          loading={q.isLoading}
          pagination={(q.data?.length ?? 0) > 20 ? { pageSize: 20 } : false}
          scroll={{ x: 720 }}
          dataSource={q.data ?? []}
          locale={{ emptyText: t("org.balance.historyEmpty") }}
          columns={[
            { title: t("org.balance.colTime"), render: (_, r) => formatDateTime(r.created_at) },
            {
              title: t("org.balance.colKind"),
              render: (_, r) =>
                r.kind === "allocate" ? (
                  <Tag color="green" variant="filled">
                    {t("org.balance.allocate")}
                  </Tag>
                ) : (
                  <Tag variant="filled">{t("org.balance.reclaim")}</Tag>
                ),
            },
            { title: t("org.members.colMember"), render: (_, r) => r.member_label },
            { title: t("org.balance.amount"), align: "right", render: (_, r) => formatMoney(r.amount) },
            { title: t("org.balance.colBy"), render: (_, r) => r.actor_label },
          ]}
        />
      )}
    </Card>
  );
}

/** Below a day of runway the row is flagged. */
const LOW_HOURS = 24;

export function FinanceTab() {
  const { t } = useTranslation();
  const { formatMoney, formatHourlyPrice } = useFormat();
  const q = useOrgFinance();
  const data = q.data;
  if (q.isError) return <TableErrorEmpty isError onRetry={() => void q.refetch()} />;
  const hours = (r: MemberFinanceOut) => {
    if (r.est_hours_left == null) return <Typography.Text type="secondary">{t("org.finance.noBurn")}</Typography.Text>;
    const text = t("org.finance.hours", { hours: r.est_hours_left });
    return r.est_hours_left < LOW_HOURS ? <Typography.Text type="danger">{text}</Typography.Text> : text;
  };
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <KpiGrid
        loading={q.isLoading}
        items={[
          <StatCard
            key="available"
            title={t("org.finance.available")}
            value={data && formatMoney(data.totals.available_balance)}
          />,
          <StatCard
            key="mtd"
            title={t("org.finance.monthToDate")}
            value={data && formatMoney(data.totals.month_to_date)}
          />,
          <StatCard
            key="burn"
            title={t("org.finance.burn")}
            value={data && formatHourlyPrice(data.totals.burn_per_hour)}
          />,
          <StatCard key="running" title={t("org.finance.running")} value={data?.totals.running_instances} />,
        ]}
      />
      <Card
        size="small"
        title={t("org.finance.title")}
        extra={
          data && (
            <Typography.Text type="secondary">
              {t("org.finance.period", { period: data.period, tz: data.billing_timezone })}
            </Typography.Text>
          )
        }
      >
        <Table<MemberFinanceOut>
          rowKey="member_id"
          size="small"
          loading={q.isLoading}
          pagination={false}
          scroll={{ x: 820 }}
          dataSource={data?.members ?? []}
          columns={[
            { title: t("org.members.colMember"), render: (_, r) => r.label },
            { title: t("org.members.colRole"), render: (_, r) => t(`org.role.${r.role}`) },
            {
              title: t("org.finance.available"),
              align: "right",
              render: (_, r) =>
                Number(r.available_balance) <= 0 ? (
                  <Typography.Text type="danger">{formatMoney(r.available_balance)}</Typography.Text>
                ) : (
                  formatMoney(r.available_balance)
                ),
            },
            { title: t("org.finance.monthToDate"), align: "right", render: (_, r) => formatMoney(r.month_to_date) },
            { title: t("org.finance.burn"), align: "right", render: (_, r) => formatHourlyPrice(r.burn_per_hour) },
            { title: t("org.finance.running"), align: "right", render: (_, r) => r.running_instances },
            { title: t("org.finance.hoursLeft"), align: "right", render: (_, r) => hours(r) },
            { title: t("org.finance.since"), render: (_, r) => formatDateTime(r.window_start) },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: space.md, marginBottom: 0 }}>
          {t("org.finance.note")}
        </Typography.Paragraph>
      </Card>
      <TransfersCard />
    </Space>
  );
}
