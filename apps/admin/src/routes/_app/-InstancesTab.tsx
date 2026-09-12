/** 实例 Tab:全局实例表(状态 / 节点 / 名称检索入 URL)+ 强制停止 / 强制回收。 */

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, getRouteApi } from "@tanstack/react-router";
import { Input, Select, Space, Table } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  controlWidth,
  formatDateTime,
  instanceStatusMap,
  layout,
  marketLabelKey,
  marketMap,
  metaOf,
  skuTierMap,
  skuVariant,
  workloadTypeMap,
  type InstanceStatus,
} from "@superdl/ui";
import { HexTag, LoadMore, TableErrorEmpty } from "@superdl/ui/components";

import { type AdminInstanceOut, isApiError, useAdminInstances, useForceStop, usePreemptInstance } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { FilterBar } from "../../components/FilterBar";
import { StatusTag } from "../../components/StatusTag";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const routeApi = getRouteApi("/_app/tenants");

export function InstancesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/tenants" });
  const role = useAdminRole();
  const writable = canWriteOps(role);
  // status/node_name/实例名检索入 URL(commit 制)
  const status = routeApi.useSearch({ select: (s) => s.istatus });
  const nodeName = routeApi.useSearch({ select: (s) => s.inode });
  const instQ = routeApi.useSearch({ select: (s) => s.iq });
  const [instInput, setInstInput] = useState(instQ ?? "");
  const [nodeInput, setNodeInput] = useState(nodeName ?? "");
  const filterKey = `${instQ ?? ""}|${nodeName ?? ""}`;
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (filterKey !== prevFilterKey) {
    setPrevFilterKey(filterKey);
    setInstInput(instQ ?? "");
    setNodeInput(nodeName ?? "");
  }
  const setUrl = (next: { istatus?: string; inode?: string; iq?: string }) =>
    void navigate({ to: "/tenants", replace: true, search: (prev) => ({ ...prev, ...next }) });
  const qc = useQueryClient();
  const instancesQ = useAdminInstances({
    ...(status ? { status } : {}),
    ...(instQ ? { q: instQ } : {}),
    ...(nodeName ? { node_name: nodeName } : {}),
  });
  const {
    data,
    queryKey,
    isLoading,
    isError,
    error,
    refetch,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
  } = instancesQ;
  const instances: AdminInstanceOut[] = data?.pages.flatMap((p) => p.items) ?? [];
  const forceStop = useForceStop();
  const preempt = usePreemptInstance();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const total = data?.pages[0]?.total ?? undefined;
  return (
    <>
      {/* 筛选条:控件 + 清除筛选 + 精确总数(服务端 total) */}
      <FilterBar
        hasFilter={Boolean(status || instQ || nodeName)}
        onClear={() => setUrl({ istatus: undefined, iq: undefined, inode: undefined })}
        count={total ?? undefined}
      >
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
      <Table<AdminInstanceOut>
        scroll={{ x: 1250 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="uuid"
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        dataSource={instances}
        columns={[
          { title: t("tenants.colInstance"), dataIndex: "name", fixed: "left", width: 180 },
          tenantColumn(t("tenants.colOwner"), 90),
          {
            title: t("tenants.colNode"),
            dataIndex: "node_name",
            width: 160,
            render: (v: string | null) => v ?? "—",
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
            // 形态列;服务行链到在线服务页
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
            // 购买模式标签取 packages/ui 映射
            title: t("tenants.colMarket"),
            dataIndex: "market",
            width: 110,
            render: (v: string, r) => {
              const labelKey = marketLabelKey(v, r.subscription?.period);
              return <HexTag color={metaOf(marketMap, v)?.color}>{labelKey ? t(labelKey) : v}</HexTag>;
            },
          },
          { title: t("tenants.colSshPort"), dataIndex: "ssh_port", width: 100 },
          { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.colActions"),
            fixed: "right",
            width: 190,
            render: (_, r) => {
              return (
                <Space>
                  <ReasonAction
                    label={t("tenants.forceStop")}
                    target={`${r.name} · ${r.uuid.slice(0, 8)}`}
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
                  {/* 强制回收:走自动抢占同一路径(通知 + 宽限窗);与强制停止分开 */}
                  <ReasonAction
                    label={t("tenants.preempt")}
                    target={`${r.name} · ${r.uuid.slice(0, 8)}`}
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
                </Space>
              );
            },
          },
        ]}
      />
      <LoadMore
        hasNextPage={hasNextPage}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={instances.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </>
  );
}
