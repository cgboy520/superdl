/** 告警中心:FilterBar(severity / 类型 / 确认状态全走服务端过滤,入 URL)+ 游标翻页;表格勾选未确认项批量确认;深链与确认闭环走 alertLink(ops/admin 可写)。 */

import {
  controlWidth,
  fontSize,
  formatDateTime,
  layout,
  POLL,
  severityMap,
  space,
  useAutoRefresh,
  useUrlFilters,
} from "@superdl/ui";
import {
  CursorTable,
  EmptyState,
  EmptyValue,
  FilterBar,
  GatedButton,
  Mono,
  PageContainer,
  StatusTag,
} from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { App, Button, Select, Space, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { adminKeys, type AlertRow, useAckAlert, useAlertPages } from "../../api";
import { BulkBar, runBulk } from "../../components/BulkBar";
import { alertLink, useAckAlertWithFeedback } from "../../lib/alertLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const SEVERITIES = ["info", "warning", "critical"] as const;
const ACK_FILTERS = ["unacked", "acked"] as const;
/** 告警流的两类来源(与后端 notify.service.ALERT_STREAM_TYPES 同表)→ 文案键。 */
const ALERT_TYPE_LABEL = {
  admin_alert: "alerts.typeAdminAlert",
  gpu_fault: "alerts.typeGpuFault",
} as const;
type AlertType = keyof typeof ALERT_TYPE_LABEL;
const ALERT_TYPES = Object.keys(ALERT_TYPE_LABEL) as AlertType[];
const isAlertType = (v: unknown): v is AlertType => typeof v === "string" && v in ALERT_TYPE_LABEL;

export const Route = createFileRoute("/_app/alerts")({
  validateSearch: (search: Record<string, unknown>): { severity?: string; acked?: string; type?: AlertType } => ({
    severity:
      typeof search.severity === "string" && (SEVERITIES as readonly string[]).includes(search.severity)
        ? search.severity
        : undefined,
    acked:
      typeof search.acked === "string" && (ACK_FILTERS as readonly string[]).includes(search.acked)
        ? search.acked
        : undefined,
    type: isAlertType(search.type) ? search.type : undefined,
  }),
  component: AlertsPage,
});

function AlertsPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/alerts" });
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const { severity, acked, type } = Route.useSearch();
  const autoRefresh = useAutoRefresh(POLL.steady);
  const alertsQ = useAlertPages(
    {
      ...(severity ? { severity } : {}),
      ...(type ? { type } : {}),
      ...(acked ? { acked: acked === "acked" } : {}),
    },
    { refetchInterval: autoRefresh.refetchInterval },
  );
  const rows = alertsQ.data?.pages.flatMap((p) => p.items) ?? [];
  const ack = useAckAlertWithFeedback();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const ackRaw = useAckAlert();
  const [selected, setSelected] = useState<number[]>([]);
  const [bulkPending, setBulkPending] = useState(false);
  const bulkAck = async () => {
    setBulkPending(true);
    try {
      const { ok, failed } = await runBulk(selected, (id) => ackRaw.mutateAsync({ alertId: id }));
      setSelected([]);
      void qc.invalidateQueries({ queryKey: adminKeys.alerts });
      if (failed > 0) message.warning(t("bulk.partial", { ok, failed }));
      else message.success(t("bulk.done", { count: ok }));
    } finally {
      setBulkPending(false);
    }
  };
  const setFilters = useCallback(
    (next: { severity?: string; acked?: string; type?: AlertType }) =>
      void navigate({ to: "/alerts", replace: true, search: (prev) => ({ ...prev, ...next }) }),
    [navigate],
  );
  const filters = useUrlFilters({
    search: { severity, acked, type },
    keys: ["severity", "acked", "type"],
    commit: setFilters,
  });

  const columns: TableColumnsType<AlertRow> = [
    {
      title: t("alerts.colSeverity"),
      dataIndex: "severity",
      fixed: "left",
      width: 110,
      render: (v: string) => <StatusTag map={severityMap} value={v} variant="text" icon />,
    },
    {
      title: t("alerts.colTitle"),
      render: (_, a) => {
        const link = alertLink(a);
        return (
          <Space orientation="vertical" size={0}>
            {link ? (
              <Link to={link.to} search={link.search}>
                {a.title}
              </Link>
            ) : (
              <Typography.Text>{a.title}</Typography.Text>
            )}
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {a.content}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: t("alerts.colTarget"),
      width: 220,
      render: (_, a) =>
        a.target_kind && a.target_id ? (
          <Space size={space.xs}>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {a.target_kind}
            </Typography.Text>
            <Mono>{a.target_id}</Mono>
          </Space>
        ) : (
          <EmptyValue />
        ),
    },
    { title: t("alerts.colTime"), dataIndex: "created_at", width: 170, render: formatDateTime },
    {
      title: t("alerts.colAck"),
      fixed: "right",
      width: 180,
      render: (_, a) =>
        a.acked_at != null ? (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("alerts.ackedShort", {
              name: a.acked_by_username ?? `#${a.acked_by ?? "-"}`,
              time: formatDateTime(a.acked_at),
            })}
          </Typography.Text>
        ) : (
          <GatedButton
            size="small"
            reason={writable ? undefined : t("overview.opsOnly")}
            loading={ack.isPending && ack.variables.alertId === a.id}
            onClick={() => ack.mutate({ alertId: a.id })}
          >
            {t("overview.ack")}
          </GatedButton>
        ),
    },
  ];

  return (
    <PageContainer
      title={t("alerts.title")}
      freshness={{
        updatedAt: alertsQ.dataUpdatedAt,
        intervalMs: autoRefresh.intervalMs,
        paused: autoRefresh.paused,
        onTogglePause: autoRefresh.toggle,
        onRefresh: () => void alertsQ.refetch(),
        refreshing: alertsQ.isRefetching,
      }}
    >
      <FilterBar hasFilter={filters.hasFilter} onClear={filters.clear}>
        <Select
          allowClear
          placeholder={t("overview.severityFilter")}
          style={{ width: controlWidth.sm }}
          value={severity}
          onChange={(v) => setFilters({ severity: v })}
          options={SEVERITIES.map((s) => ({
            value: s,
            label: t(severityMap[s].labelKey),
          }))}
        />
        <Select
          allowClear
          placeholder={t("alerts.typeFilter")}
          style={{ width: controlWidth.sm }}
          value={type}
          onChange={(v) => setFilters({ type: v })}
          options={ALERT_TYPES.map((v) => ({ value: v, label: t(ALERT_TYPE_LABEL[v]) }))}
        />
        <Select
          allowClear
          placeholder={t("alerts.ackFilter")}
          style={{ width: controlWidth.sm }}
          value={acked}
          onChange={(v) => setFilters({ acked: v })}
          options={[
            { value: "unacked", label: t("alerts.ackUnacked") },
            { value: "acked", label: t("alerts.ackAcked") },
          ]}
        />
      </FilterBar>
      <BulkBar count={selected.length} onClear={() => setSelected([])}>
        <Button type="primary" size="small" loading={bulkPending} onClick={() => void bulkAck()}>
          {t("bulk.ackSelected", { count: selected.length })}
        </Button>
      </BulkBar>
      <CursorTable<AlertRow>
        query={alertsQ}
        rows={rows}
        rowKey="id"
        columns={columns}
        scroll={{ x: 1000 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowSelection={
          writable
            ? {
                selectedRowKeys: selected,
                onChange: (keys) => setSelected(keys.map(Number)),
                getCheckboxProps: (a) => ({ disabled: a.acked_at != null, name: a.title }),
                fixed: true,
              }
            : undefined
        }
        emptyNode={
          filters.hasFilter ? (
            <EmptyState
              scene="search"
              compact
              description={t("alerts.empty")}
              secondaryAction={
                <Button size="small" onClick={filters.clear}>
                  {t("filter.clear", { ns: "shared" })}
                </Button>
              }
            />
          ) : (
            <EmptyState scene="notification" compact description={t("shell.noAlerts")} />
          )
        }
      />
    </PageContainer>
  );
}
