/** 告警中心:severity 服务端过滤、确认状态客户端过滤,入 URL;深链与确认闭环走 alertLink(ops/admin 可写)。 */

import { controlWidth, fontSize, formatDateTime, POLL, space, useAutoRefresh } from "@superdl/ui";
import { EmptyState, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { App, Badge, Button, Checkbox, List, Select, Space, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type AlertRow, useAckAlert, useAlerts } from "../../api";
import { BulkBar, runBulk } from "../../components/BulkBar";
import { alertLink, SEVERITY_LABEL_KEY, severityColor, useAckAlertWithFeedback } from "../../lib/alertLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const SEVERITIES = ["info", "warning", "critical"] as const;
const ACK_FILTERS = ["unacked", "acked"] as const;

export const Route = createFileRoute("/_app/alerts")({
  validateSearch: (search: Record<string, unknown>): { severity?: string; acked?: string } => ({
    severity:
      typeof search.severity === "string" && (SEVERITIES as readonly string[]).includes(search.severity)
        ? search.severity
        : undefined,
    acked:
      typeof search.acked === "string" && (ACK_FILTERS as readonly string[]).includes(search.acked)
        ? search.acked
        : undefined,
  }),
  component: AlertsPage,
});

function AlertsPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/alerts" });
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const { severity, acked } = Route.useSearch();
  // severity 服务端参数;确认状态客户端过滤(200 条窗口)
  const autoRefresh = useAutoRefresh(POLL.steady);
  const alertsQ = useAlerts(severity ? { severity } : undefined, {
    refetchInterval: autoRefresh.refetchInterval,
  });
  const rows = (alertsQ.data ?? []).filter((a) =>
    acked === "acked" ? a.acked_at != null : acked === "unacked" ? a.acked_at == null : true,
  );
  // 确认闭环见 lib/alertLink
  const ack = useAckAlertWithFeedback();
  // 批量确认:勾选未确认项,逐条并发(后端无批量端点)
  const { message } = App.useApp();
  const qc = useQueryClient();
  const ackRaw = useAckAlert();
  const [selected, setSelected] = useState<number[]>([]);
  const [bulkPending, setBulkPending] = useState(false);
  const unacked = rows.filter((a) => a.acked_at == null);
  const allSelected = unacked.length > 0 && unacked.every((a) => selected.includes(a.id));
  const bulkAck = async () => {
    setBulkPending(true);
    try {
      const { ok, failed } = await runBulk(selected, (id) => ackRaw.mutateAsync({ alertId: id }));
      setSelected([]);
      void qc.invalidateQueries({ queryKey: ["admin", "alerts"] });
      if (failed > 0) message.warning(t("bulk.partial", { ok, failed }));
      else message.success(t("bulk.done", { count: ok }));
    } finally {
      setBulkPending(false);
    }
  };
  const setFilters = (next: { severity?: string; acked?: string }) =>
    void navigate({ to: "/alerts", replace: true, search: (prev) => ({ ...prev, ...next }) });

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
      extra={
        <Space wrap>
          <Select
            allowClear
            placeholder={t("overview.severityFilter")}
            style={{ width: controlWidth.sm }}
            value={severity}
            onChange={(v) => setFilters({ severity: v })}
            options={SEVERITIES.map((s) => ({
              value: s,
              label: t(SEVERITY_LABEL_KEY[s]),
            }))}
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
        </Space>
      }
    >
      {writable && unacked.length > 0 && (
        <div style={{ marginBottom: space.sm, paddingInline: space.md }}>
          <Checkbox
            checked={allSelected}
            indeterminate={selected.length > 0 && !allSelected}
            onChange={(e) => setSelected(e.target.checked ? unacked.map((a) => a.id) : [])}
          >
            {t("bulk.selectAllUnacked", { count: unacked.length })}
          </Checkbox>
        </div>
      )}
      <BulkBar count={selected.length} onClear={() => setSelected([])}>
        <Button type="primary" size="small" loading={bulkPending} onClick={() => void bulkAck()}>
          {t("bulk.ackSelected", { count: selected.length })}
        </Button>
      </BulkBar>
      <List
        loading={alertsQ.isLoading}
        dataSource={rows}
        locale={{
          emptyText: alertsQ.isError ? (
            <TableErrorEmpty isError onRetry={() => void alertsQ.refetch()} />
          ) : (
            <EmptyState scene="notification" compact description={t("alerts.empty")} />
          ),
        }}
        renderItem={(a: AlertRow) => {
          const link = alertLink(a);
          return (
            <List.Item
              style={{ paddingInline: space.md }}
              actions={
                a.acked_at == null
                  ? [
                      <Tooltip key="ack" title={writable ? "" : t("overview.opsOnly")}>
                        <Button
                          size="small"
                          disabled={!writable}
                          loading={ack.isPending && ack.variables.alertId === a.id}
                          onClick={() => ack.mutate({ alertId: a.id })}
                        >
                          {t("overview.ack")}
                        </Button>
                      </Tooltip>,
                    ]
                  : undefined
              }
            >
              <List.Item.Meta
                avatar={
                  writable && a.acked_at == null ? (
                    <Checkbox
                      aria-label={a.title}
                      checked={selected.includes(a.id)}
                      onChange={(e) =>
                        setSelected((s) => (e.target.checked ? [...s, a.id] : s.filter((id) => id !== a.id)))
                      }
                    />
                  ) : undefined
                }
                title={
                  <Space size={8} wrap>
                    <Badge color={severityColor(a.severity)} />
                    {link ? (
                      <Link to={link.to} search={link.search}>
                        {a.title}
                      </Link>
                    ) : (
                      <Typography.Text>{a.title}</Typography.Text>
                    )}
                    {a.acked_at != null && (
                      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                        {t("alerts.ackedBy", {
                          name: a.acked_by_username ?? `#${a.acked_by ?? "-"}`,
                          time: formatDateTime(a.acked_at),
                        })}
                      </Typography.Text>
                    )}
                  </Space>
                }
                description={
                  <>
                    <div>{a.content}</div>
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {formatDateTime(a.created_at)}
                    </Typography.Text>
                  </>
                }
              />
            </List.Item>
          );
        }}
      />
    </PageContainer>
  );
}
