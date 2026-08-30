/** 告警中心:全量告警列表(顶栏 AlertBell Popover 的完整版)。
 *  severity 服务端过滤、确认状态客户端过滤,全部入 URL(运营面转达的视图必须可还原);
 *  深链目标与 AlertBell/总览告警流共用 alertLink;确认闭环与两者同一范式(ops/admin 可写)。 */

import { fontSize, formatDateTime, space } from "@superdl/ui";
import { EmptyState, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Badge, Button, List, Select, Space, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { type AlertRow, useAlerts } from "../../api";
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
  // severity 走服务端参数;确认状态为客户端过滤(端点无 acked 参数,200 条窗口内本地过滤)
  const alertsQ = useAlerts(severity ? { severity } : undefined, { refetchInterval: 30_000 });
  const rows = (alertsQ.data ?? []).filter((a) =>
    acked === "acked" ? a.acked_at != null : acked === "unacked" ? a.acked_at == null : true,
  );
  // 确认闭环同 AlertBell/总览告警流范式(见 lib/alertLink)
  const ack = useAckAlertWithFeedback();
  const setFilters = (next: { severity?: string; acked?: string }) =>
    void navigate({ to: "/alerts", replace: true, search: (prev) => ({ ...prev, ...next }) });

  return (
    <PageContainer
      title={t("alerts.title")}
      extra={
        <Space wrap>
          <Select
            allowClear
            placeholder={t("overview.severityFilter")}
            style={{ width: 140 }}
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
            style={{ width: 140 }}
            value={acked}
            onChange={(v) => setFilters({ acked: v })}
            options={[
              { value: "unacked", label: t("alerts.ackUnacked") },
              { value: "acked", label: t("alerts.ackAcked") },
            ]}
          />
          <Button onClick={() => void alertsQ.refetch()} loading={alertsQ.isRefetching}>
            {t("common.refresh")}
          </Button>
        </Space>
      }
    >
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
                          loading={ack.isPending && ack.variables?.alertId === a.id}
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
