/** Private network: gateways that connect the tenant's own network (VPN) to their instances. List + create / edit drawer + detail drawer (?gw=<uuid>: connection facts, usage, forwards, log). Organization owners and admins switch to the organization's gateways (?scope=org); members see their instances attached to organization gateways and can detach them. Availability, limits and the platform egress addresses come from /netgw/config. */

import type { GatewayOut, MyAttachmentOut } from "@superdl/api-client";
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
  useConfirm,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { App, Button, Card, Segmented, Space, Table, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useDeleteGateway, useDeleteOrgGateway, useDetachOwn } from "../api/mutations";
import { useGateways, useMyAttachments, useMyOrg, useNetgwConfig, useOrgGateways } from "../api/queries";
import { GatewayDetailDrawer } from "../components/network/GatewayDetailDrawer";
import { type AnyGateway, type GatewayScope, GatewayFormDrawer } from "../components/network/GatewayFormDrawer";
import { requireAuth } from "../lib/guard";

interface NetworkSearch {
  gw?: string;
  scope?: "org";
}

/** Whitelist: a 32-hex gateway uuid and the organization scope. */
export function networkValidateSearch(search: Record<string, unknown>): NetworkSearch {
  const out: NetworkSearch = {};
  if (typeof search.gw === "string" && /^[0-9a-f]{32}$/.test(search.gw)) out.gw = search.gw;
  if (search.scope === "org") out.scope = "org";
  return out;
}

export const Route = createFileRoute("/_console/network")({
  beforeLoad: requireAuth,
  validateSearch: networkValidateSearch,
  component: NetworkPage,
});

/** The caller's instances on organization gateways: the owner can always detach them. */
function MyOrgAttachmentsCard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const mine = useMyAttachments();
  const detach = useDetachOwn({ onSuccess: () => message.success(t("network.l3.detached")) });
  const rows = (mine.data ?? []).filter((a) => a.scope === "org");
  if (rows.length === 0) return null;
  return (
    <Card size="small" title={t("network.org.myAttachments")}>
      <Table<MyAttachmentOut>
        rowKey="instance_uuid"
        size="small"
        pagination={false}
        scroll={{ x: 640 }}
        dataSource={rows}
        columns={[
          { title: t("network.l3.colInstance"), render: (_, a) => a.instance_name },
          { title: t("network.org.colGateway"), render: (_, a) => a.gateway_name },
          { title: t("network.l3.colAddress"), render: (_, a) => <Mono>{a.address}</Mono> },
          {
            title: t("network.l3.colState"),
            render: (_, a) =>
              a.pending_restart ? (
                <Typography.Text type="warning">{t("network.l3.pendingRestart")}</Typography.Text>
              ) : (
                <Typography.Text type="secondary">{t("network.l3.applied")}</Typography.Text>
              ),
          },
          {
            title: t("network.col.actions"),
            render: (_, a) => (
              <Button
                size="small"
                danger
                onClick={() =>
                  confirm({
                    title: t("network.l3.detachTitle", { name: a.instance_name }),
                    consequences: [t("network.org.detachOwnConsequence", { gateway: a.gateway_name })],
                    okText: t("network.l3.detachOk"),
                    cancelText: t("network.cancel"),
                    danger: true,
                    onOk: () => detach.mutate(a.instance_uuid),
                  })
                }
              >
                {t("network.l3.detach")}
              </Button>
            ),
          },
        ]}
      />
    </Card>
  );
}

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
  const search = Route.useSearch();
  const openUuid = search.gw;
  const auto = useAutoRefresh(POLL.steady);
  const cfg = useNetgwConfig();
  const my = useMyOrg();
  const orgAdmin = my.data?.role === "owner" || my.data?.role === "admin";
  const scope: GatewayScope = orgAdmin && search.scope === "org" ? "org" : "personal";
  const org = scope === "org";
  const poll = {
    refetchInterval: (q: { state: { data: AnyGateway[] | undefined } }) =>
      (q.state.data ?? []).some((g) => isTransientNetgwState(g.state)) ? POLL.transient : auto.refetchInterval,
  };
  const personalList = useGateways({ ...poll, enabled: !org });
  const orgList = useOrgGateways({ ...poll, enabled: org });
  const list = org ? orgList : personalList;
  const [form, setForm] = useState<{ open: boolean; gateway: AnyGateway | null }>({ open: false, gateway: null });
  const [deleteTarget, setDeleteTarget] = useState<AnyGateway | null>(null);
  const deleted = () => {
    message.success(t("network.deleteStarted"));
    setDeleteTarget(null);
    void navigate({ search: org ? { scope: "org" } : {}, replace: true });
  };
  const del = useDeleteGateway({ onSuccess: deleted });
  const delOrg = useDeleteOrgGateway({ onSuccess: deleted });

  const gateways: AnyGateway[] = list.data ?? [];
  const enabled = cfg.data?.enabled ?? false;
  const max = (org ? cfg.data?.max_org_gateways : cfg.data?.max_gateways) ?? 0;
  const createReason = !enabled
    ? t("network.unavailable")
    : gateways.length >= max
      ? t(org ? "network.org.quotaReached" : "network.quotaReached", { max })
      : undefined;
  const openDetail = (uuid: string | undefined) =>
    void navigate({
      search: { ...(org ? { scope: "org" as const } : {}), ...(uuid ? { gw: uuid } : {}) },
      replace: true,
    });
  const setScope = (next: GatewayScope) =>
    void navigate({ search: next === "org" ? { scope: "org" } : {}, replace: true });
  const detail = gateways.find((g) => g.uuid === openUuid) ?? null;
  const personalOf = (g: AnyGateway): GatewayOut | null => ("socks_url" in g ? g : null);

  const createButton = (
    <GatedButton type="primary" reason={createReason} onClick={() => setForm({ open: true, gateway: null })}>
      {org ? t("network.org.create") : t("network.create")}
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
            {orgAdmin && (
              <Segmented<GatewayScope>
                value={scope}
                onChange={setScope}
                options={[
                  { value: "personal", label: t("network.org.scopePersonal") },
                  { value: "org", label: t("network.org.scopeOrg", { name: my.data?.org?.name ?? "" }) },
                ]}
              />
            )}
            <EgressCard ips={cfg.data?.egress_ips ?? []} />
            {!org && <MyOrgAttachmentsCard />}
            <Card>
              {list.isError ? (
                <TableErrorEmpty isError onRetry={() => void list.refetch()} />
              ) : gateways.length === 0 && !list.isLoading ? (
                <EmptyState description={org ? t("network.org.empty") : t("network.empty")} action={createButton} />
              ) : (
                <Table<AnyGateway>
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
                    ...(org
                      ? [
                          {
                            title: t("network.org.colAttached"),
                            align: "right" as const,
                            render: (_: unknown, g: AnyGateway) => g.l3?.attachments.length ?? 0,
                          },
                        ]
                      : [
                          {
                            title: t("network.col.forwards"),
                            align: "right" as const,
                            render: (_: unknown, g: AnyGateway) => personalOf(g)?.forwards.length ?? 0,
                          },
                          {
                            title: t("network.col.socks"),
                            render: (_: unknown, g: AnyGateway) => <Mono>{personalOf(g)?.socks_url ?? ""}</Mono>,
                          },
                        ]),
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
        scope={scope}
        onClose={() => setForm({ open: false, gateway: null })}
        onSaved={(g, created) => {
          setForm({ open: false, gateway: null });
          if (created) openDetail(g.uuid);
        }}
      />
      <GatewayDetailDrawer
        gateway={detail}
        scope={scope}
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
        loading={del.isPending || delOrg.isPending}
        onConfirm={() => deleteTarget && (org ? delOrg : del).mutate(deleteTarget.uuid)}
        onCancel={() => setDeleteTarget(null)}
      />
    </PageContainer>
  );
}
