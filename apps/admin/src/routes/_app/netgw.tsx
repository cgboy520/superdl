/** Private-network gateways (all tenants): one table, optionally narrowed to a tenant (?user_id=); the only disposal is deletion with a reason. Secrets are never returned by the API. */

import { formatDateTime, metaOf, netgwStateMap, POLL, useAutoRefresh } from "@superdl/ui";
import { EmptyState, HexTag, Mono, PageContainer, RowActions, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Card, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useTranslation } from "react-i18next";

import { type AdminGatewayOut, useAdminDeleteGateway, useAdminGateways } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export interface NetgwSearch {
  user_id?: number;
}

export function netgwValidateSearch(search: Record<string, unknown>): NetgwSearch {
  const uid = Number(search.user_id);
  return Number.isInteger(uid) && uid > 0 ? { user_id: uid } : {};
}

export const Route = createFileRoute("/_app/netgw")({
  validateSearch: netgwValidateSearch,
  component: NetgwPage,
});

function peerOf(g: AdminGatewayOut): string {
  const tn = g.tunnel;
  switch (tn.protocol) {
    case "wireguard":
      return tn.endpoint;
    case "ipsec":
      return tn.peer;
    case "openvpn":
      return tn.remotes.join(", ");
    case "openconnect":
      return tn.server;
  }
}

function NetgwPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const { user_id: userId } = Route.useSearch();
  const writable = canWriteOps(useAdminRole());
  const auto = useAutoRefresh(POLL.steady);
  const q = useAdminGateways(userId, { refetchInterval: auto.refetchInterval });
  const del = useAdminDeleteGateway();

  const columns: TableColumnsType<AdminGatewayOut> = [
    {
      title: t("netgw.colGateway"),
      fixed: "left",
      width: 200,
      render: (_, g) => (
        <Space orientation="vertical" size={0}>
          <span>{g.name}</span>
          <Typography.Text type="secondary">
            <Mono truncate={8}>{g.uuid}</Mono>
          </Typography.Text>
        </Space>
      ),
    },
    tenantColumn<AdminGatewayOut>(t("tenants.colOwner"), 90),
    { title: t("netgw.colProtocol"), width: 110, render: (_, g) => t(`netgw.protocol.${g.protocol}`) },
    {
      title: t("netgw.colState"),
      width: 130,
      render: (_, g) => {
        const m = metaOf(netgwStateMap, g.state);
        return (
          <Space orientation="vertical" size={0}>
            <HexTag color={m?.color}>{m ? t(m.labelKey) : g.state}</HexTag>
            <Typography.Text type="secondary">{g.pod_phase ?? "—"}</Typography.Text>
          </Space>
        );
      },
    },
    { title: t("netgw.colPeer"), render: (_, g) => <Mono>{peerOf(g)}</Mono> },
    { title: t("netgw.colRemote"), render: (_, g) => <Mono>{g.remote_cidrs.join(", ")}</Mono> },
    { title: t("netgw.colForwards"), width: 80, align: "right", render: (_, g) => g.forwards.length },
    {
      title: t("netgw.colChecked"),
      width: 170,
      render: (_, g) => (g.checked_at ? formatDateTime(g.checked_at) : "—"),
    },
    { title: t("netgw.colCreated"), dataIndex: "created_at", width: 170, render: formatDateTime },
    {
      title: t("netgw.colActions"),
      width: 110,
      fixed: "right",
      render: (_, g) => (
        <RowActions
          primary={
            <ReasonAction
              label={t("netgw.delete")}
              target={`${g.name} · ${g.uuid}`}
              danger
              title={t("netgw.deleteTitle")}
              confirmText={t("netgw.deleteConfirm", { name: g.name, user: g.user_id })}
              disabled={!writable || g.state === "deleting"}
              disabledReason={!writable ? t("tenants.noPermission") : t("netgw.alreadyDeleting")}
              onSubmit={async (reason) => {
                await del.mutateAsync({ uuid: g.uuid, data: { reason } });
                void q.refetch();
              }}
            />
          }
        />
      ),
    },
  ];

  return (
    <PageContainer
      width="full"
      title={t("menu.netgw")}
      tags={
        userId ? (
          <Tag closable={false}>
            <Link to="/netgw" search={{}}>
              {t("netgw.tenantFilter", { id: userId })}
            </Link>
          </Tag>
        ) : undefined
      }
      freshness={{
        updatedAt: q.dataUpdatedAt,
        intervalMs: auto.intervalMs,
        paused: auto.paused,
        onTogglePause: auto.toggle,
        onRefresh: () => void q.refetch(),
        refreshing: q.isRefetching,
      }}
    >
      <Card>
        {q.isError ? (
          <TableErrorEmpty isError onRetry={() => void q.refetch()} />
        ) : (q.data ?? []).length === 0 && !q.isLoading ? (
          <EmptyState description={t("netgw.empty")} />
        ) : (
          <Table<AdminGatewayOut>
            rowKey="uuid"
            loading={q.isLoading}
            pagination={false}
            scroll={{ x: 1300 }}
            dataSource={q.data ?? []}
            columns={columns}
          />
        )}
      </Card>
    </PageContainer>
  );
}
