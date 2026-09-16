/** Instances tab: global instance table (status / node / name search in the URL) + force stop (primary action) / force reclaim (more). */

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, getRouteApi } from "@tanstack/react-router";
import { Button, Input, Select, Space, Typography } from "antd";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import {
  controlWidth,
  flattenPages,
  fontSize,
  formatDateTime,
  instanceStatusMap,
  layout,
  marketLabelKey,
  marketMap,
  metaOf,
  skuTierMap,
  skuVariant,
  useUrlCommittedInput,
  workloadTypeMap,
  type InstanceStatus,
} from "@superdl/ui";
import { CursorTable, EmptyState, FilterBar, HexTag, Mono, RowActions, RowMoreMenu } from "@superdl/ui/components";

import { type AdminInstanceOut, useAdminInstances, useForceStop, usePreemptInstance } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "@superdl/ui/components";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const routeApi = getRouteApi("/_app/tenants");

export function InstancesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/tenants" });
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const status = routeApi.useSearch({ select: (s) => s.istatus });
  const nodeName = routeApi.useSearch({ select: (s) => s.inode });
  const instQ = routeApi.useSearch({ select: (s) => s.iq });
  const setUrl = useCallback(
    (next: { istatus?: string; inode?: string; iq?: string }) =>
      void navigate({ to: "/tenants", replace: true, search: (prev) => ({ ...prev, ...next }) }),
    [navigate],
  );
  const { value: instInput, setValue: setInstInput } = useUrlCommittedInput(
    instQ,
    useCallback((next: string | undefined) => setUrl({ iq: next }), [setUrl]),
  );
  const { value: nodeInput, setValue: setNodeInput } = useUrlCommittedInput(
    nodeName,
    useCallback((next: string | undefined) => setUrl({ inode: next }), [setUrl]),
  );
  const qc = useQueryClient();
  const instancesQ = useAdminInstances({
    ...(status ? { status } : {}),
    ...(instQ ? { q: instQ } : {}),
    ...(nodeName ? { node_name: nodeName } : {}),
  });
  const { data, queryKey } = instancesQ;
  const instances = flattenPages(data);
  const forceStop = useForceStop();
  const preempt = usePreemptInstance();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const total = data?.pages[0]?.total ?? undefined;
  const hasFilter = Boolean(status || instQ || nodeName);
  const clearFilters = () => setUrl({ istatus: undefined, iq: undefined, inode: undefined });
  return (
    <>
      <FilterBar hasFilter={hasFilter} onClear={clearFilters} count={total ?? undefined}>
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: controlWidth.sm }}
          value={status}
          onChange={(v) => setUrl({ istatus: v })}
          options={Object.entries(instanceStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <Input.Search
          allowClear
          placeholder={t("tenants.searchInstancePlaceholder")}
          style={{ width: controlWidth.md }}
          value={instInput}
          onChange={(e) => setInstInput(e.target.value)}
          onSearch={(v) => setUrl({ iq: v || undefined })}
        />
        <Input.Search
          allowClear
          placeholder={t("tenants.searchNodePlaceholder")}
          style={{ width: controlWidth.md }}
          value={nodeInput}
          onChange={(e) => setNodeInput(e.target.value)}
          onSearch={(v) => setUrl({ inode: v || undefined })}
        />
      </FilterBar>
      <CursorTable<AdminInstanceOut>
        query={instancesQ}
        rows={instances}
        emptyNode={
          <EmptyState
            scene={hasFilter ? "search" : "list"}
            compact
            secondaryAction={
              hasFilter ? (
                <Button size="small" onClick={clearFilters}>
                  {t("filter.clear", { ns: "shared" })}
                </Button>
              ) : undefined
            }
          />
        }
        scroll={{ x: 1250 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="uuid"
        columns={[
          {
            title: t("tenants.colInstance"),
            fixed: "left",
            width: 180,
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <span>{r.name}</span>
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  <Mono truncate={8}>{r.uuid}</Mono>
                </Typography.Text>
              </Space>
            ),
          },
          tenantColumn(t("tenants.colOwner"), 90),
          {
            title: t("tenants.colNode"),
            dataIndex: "node_name",
            width: 160,
            render: (v: string | null) => (v ? <Mono>{v}</Mono> : "—"),
          },
          {
            title: t("tenants.colStatus"),
            dataIndex: "status",
            render: (v: InstanceStatus) => <StatusTag map={instanceStatusMap} value={v} variant="badge" />,
          },
          {
            title: t("tenants.colSpec"),
            render: (_, r) => {
              const tm = metaOf(skuTierMap, skuVariant(r.spec.tier as string, r.spec.pool_label as string));
              return (
                <Space>
                  <span>
                    {String(r.spec.gpu_model)} × {r.gpu_count}
                  </span>
                  <HexTag color={tm?.color}>{tm ? t(tm.labelKey) : String(r.spec.tier)}</HexTag>
                </Space>
              );
            },
          },
          {
            title: t("tenants.colWorkload"),
            width: 110,
            render: (_, r) => {
              const wm = metaOf(workloadTypeMap, r.workload_type);
              const tag = <HexTag color={wm?.color}>{wm ? t(wm.labelKey) : r.workload_type}</HexTag>;
              return r.service_slug ? (
                <Link to="/services" search={{ q: r.service_slug }}>
                  {tag}
                </Link>
              ) : (
                tag
              );
            },
          },
          {
            title: t("tenants.colMarket"),
            dataIndex: "market",
            width: 110,
            render: (v: string, r) => {
              const labelKey = marketLabelKey(v, r.subscription?.period);
              return <HexTag color={metaOf(marketMap, v)?.color}>{labelKey ? t(labelKey) : v}</HexTag>;
            },
          },
          { title: t("tenants.colSshPort"), dataIndex: "ssh_port", width: 100, align: "right" },
          { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.colActions"),
            fixed: "right",
            width: 170,
            render: (_, r) => {
              const target = `${r.name} · ${r.uuid.slice(0, 8)}`;
              return (
                <RowActions
                  primary={
                    <ReasonAction
                      label={t("tenants.forceStop")}
                      target={target}
                      danger
                      title={t("tenants.forceStopTitle")}
                      confirmText={t("tenants.forceStopConfirm", { name: r.name, id: r.uuid.slice(0, 8) })}
                      disabled={!writable || r.status !== "running"}
                      disabledReason={!writable ? t("tenants.noPermission") : t("tenants.forceStopNeedsRunning")}
                      onSubmit={async (reason) => {
                        await forceStop.mutateAsync({ uuid: r.uuid, data: { reason } });
                        refresh();
                      }}
                    />
                  }
                  more={
                    <RowMoreMenu>
                      <ReasonAction
                        label={t("tenants.preempt")}
                        type="text"
                        target={target}
                        danger
                        title={t("tenants.preemptTitle")}
                        confirmText={t("tenants.preemptConfirm", {
                          name: r.name,
                          id: r.uuid.slice(0, 8),
                        })}
                        disabled={!writable || r.market !== "spot" || r.status !== "running"}
                        disabledReason={
                          !writable
                            ? t("tenants.noPermission")
                            : r.market !== "spot"
                              ? t("tenants.preemptNeedsSpot")
                              : t("tenants.preemptNeedsRunning")
                        }
                        onSubmit={async (reason) => {
                          await preempt.mutateAsync({ uuid: r.uuid, data: { reason } });
                          refresh();
                        }}
                      />
                    </RowMoreMenu>
                  }
                />
              );
            },
          },
        ]}
      />
    </>
  );
}
