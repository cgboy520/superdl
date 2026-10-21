/** Private network: gateways that connect the tenant's own network (VPN) to their instances. List + create / edit drawer + detail drawer (?gw=<uuid>: connection facts, usage, forwards, log). Availability, limits and the platform egress addresses come from /netgw/config. */

import type { GatewayOut } from "@superdl/api-client";
import { formatDateTime, isTransientNetgwState, netgwStateMap, POLL, space, useAutoRefresh } from "@superdl/ui";
import {
  CopyField,
  EmptyState,
  GatedButton,
  Mono,
  PageContainer,
  RowActions,
  StatusTag,
  TableErrorEmpty,
  TypeConfirmModal,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { App, Button, Card, Space, Table, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useDeleteGateway } from "../api/mutations";
import { useGateways, useNetgwConfig } from "../api/queries";
import { GatewayDetailDrawer } from "../components/network/GatewayDetailDrawer";
import { GatewayFormDrawer } from "../components/network/GatewayFormDrawer";
import { requireAuth } from "../lib/guard";

interface NetworkSearch {
  gw?: string;
}

export const Route = createFileRoute("/_console/network")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): NetworkSearch =>
    typeof search.gw === "string" && /^[0-9a-f]{32}$/.test(search.gw) ? { gw: search.gw } : {},
  component: NetworkPage,
});

function EgressCard({ ips }: { ips: string[] }) {
  const { t } = useTranslation();
  return (
    <Card size="small" title={t("network.egressTitle")}>
      <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
        {ips.length ? (
          <Space wrap size={space.sm}>
            {ips.map((ip) => (
              <CopyField key={ip} value={ip} mono />
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">{t("network.egressUnknown")}</Typography.Text>
        )}
        <Typography.Text type="secondary">{t("network.egressHint")}</Typography.Text>
      </Space>
    </Card>
  );
}

function NetworkPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate({ from: "/network" });
  const { gw: openUuid } = Route.useSearch();
  const auto = useAutoRefresh(POLL.steady);
  const cfg = useNetgwConfig();
  const list = useGateways({
    refetchInterval: (q) =>
      (q.state.data ?? []).some((g) => isTransientNetgwState(g.state)) ? POLL.transient : auto.refetchInterval,
  });
  const [form, setForm] = useState<{ open: boolean; gateway: GatewayOut | null }>({ open: false, gateway: null });
  const [deleteTarget, setDeleteTarget] = useState<GatewayOut | null>(null);
  const del = useDeleteGateway({
    onSuccess: () => {
      message.success(t("network.deleteStarted"));
      setDeleteTarget(null);
      void navigate({ search: {}, replace: true });
    },
  });

  const gateways = list.data ?? [];
  const enabled = cfg.data?.enabled ?? false;
  const max = cfg.data?.max_gateways ?? 0;
  const createReason = !enabled
    ? t("network.unavailable")
    : gateways.length >= max
      ? t("network.quotaReached", { max })
      : undefined;
  const openDetail = (uuid: string | undefined) => void navigate({ search: uuid ? { gw: uuid } : {}, replace: true });
  const detail = gateways.find((g) => g.uuid === openUuid) ?? null;

  const createButton = (
    <GatedButton type="primary" reason={createReason} onClick={() => setForm({ open: true, gateway: null })}>
      {t("network.create")}
    </GatedButton>
  );

  return (
    <PageContainer
      title={t("network.title")}
      description={t("network.description")}
      extra={createButton}
      freshness={{
        updatedAt: list.dataUpdatedAt,
        intervalMs: auto.intervalMs,
        paused: auto.paused,
        onTogglePause: auto.toggle,
        onRefresh: () => void list.refetch(),
        refreshing: list.isRefetching,
      }}
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        {cfg.data && !enabled ? (
          <Card>
            <EmptyState
              description={t("network.unavailableEmpty")}
              action={
                <Link to="/support">
                  <Button>{t("network.contactSupport")}</Button>
                </Link>
              }
            />
          </Card>
        ) : (
          <>
            <EgressCard ips={cfg.data?.egress_ips ?? []} />
            <Card>
              {list.isError ? (
                <TableErrorEmpty isError onRetry={() => void list.refetch()} />
              ) : gateways.length === 0 && !list.isLoading ? (
                <EmptyState description={t("network.empty")} action={createButton} />
              ) : (
                <Table<GatewayOut>
                  rowKey="uuid"
                  loading={list.isLoading}
                  pagination={false}
                  scroll={{ x: 900 }}
                  dataSource={gateways}
                  columns={[
                    {
                      title: t("network.col.name"),
                      render: (_, g) => (
                        <Button type="link" style={{ padding: 0 }} onClick={() => openDetail(g.uuid)}>
                          {g.name}
                        </Button>
                      ),
                    },
                    { title: t("network.col.protocol"), render: (_, g) => t(`network.protocol.${g.protocol}`) },
                    {
                      title: t("network.col.state"),
                      render: (_, g) => <StatusTag map={netgwStateMap} value={g.state} variant="badge" />,
                    },
                    { title: t("network.col.remote"), render: (_, g) => <Mono>{g.remote_cidrs.join(", ")}</Mono> },
                    { title: t("network.col.forwards"), align: "right", render: (_, g) => g.forwards.length },
                    { title: t("network.col.socks"), render: (_, g) => <Mono>{g.socks_url}</Mono> },
                    { title: t("network.col.created"), render: (_, g) => formatDateTime(g.created_at) },
                    {
                      title: t("network.col.actions"),
                      render: (_, g) => {
                        const deleting = g.state === "deleting";
                        return (
                          <RowActions
                            primary={
                              <Button size="small" onClick={() => openDetail(g.uuid)}>
                                {t("network.manage")}
                              </Button>
                            }
                            more={[
                              {
                                key: "edit",
                                label: t("network.edit"),
                                reason: deleting ? t("network.deletingReason") : undefined,
                                onClick: () => setForm({ open: true, gateway: g }),
                              },
                              {
                                key: "delete",
                                label: t("network.delete"),
                                danger: true,
                                reason: deleting ? t("network.deletingReason") : undefined,
                                onClick: () => setDeleteTarget(g),
                              },
                            ]}
                          />
                        );
                      },
                    },
                  ]}
                />
              )}
            </Card>
          </>
        )}
      </Space>

      <GatewayFormDrawer
        open={form.open}
        gateway={form.gateway}
        onClose={() => setForm({ open: false, gateway: null })}
        onSaved={(g, created) => {
          setForm({ open: false, gateway: null });
          if (created) openDetail(g.uuid);
        }}
      />
      <GatewayDetailDrawer
        gateway={detail}
        egressIps={cfg.data?.egress_ips ?? []}
        socksPort={cfg.data?.socks_port ?? 1080}
        maxForwards={cfg.data?.max_forwards ?? 0}
        onClose={() => openDetail(undefined)}
        onEdit={(g) => setForm({ open: true, gateway: g })}
      />
      <TypeConfirmModal
        open={Boolean(deleteTarget)}
        title={t("network.deleteTitle")}
        body={t("network.deleteBody", { name: deleteTarget?.name ?? "" })}
        targetName={deleteTarget?.name ?? ""}
        confirmLabel={t("network.confirmDelete")}
        cancelLabel={t("network.cancel")}
        loading={del.isPending}
        onConfirm={() => deleteTarget && del.mutate(deleteTarget.uuid)}
        onCancel={() => setDeleteTarget(null)}
      />
    </PageContainer>
  );
}
