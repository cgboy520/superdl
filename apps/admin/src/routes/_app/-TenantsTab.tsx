/** 租户 Tab:检索(落审计)/ 状态筛选 / 注册排序 + 行内冻结·解冻 + 租户抽屉(?tenant=)。 */

import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, getRouteApi } from "@tanstack/react-router";
import { Button, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { adminColors, formatDateTime, layout } from "@superdl/ui";
import { LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { type TenantRow, isApiError, useFreezeTenant, useTenants, useUnfreezeTenant } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { TenantLink } from "../../components/TenantLink";
import { useUrlCommittedInput } from "../../lib/useUrlCommittedInput";
import { REASON_MAX_LEN } from "../../lib/validators";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { type DrawerTab, TenantDrawer } from "./-TenantDrawer";

const routeApi = getRouteApi("/_app/tenants");

export function TenantsTab() {
  const { t: tt } = useTranslation();
  const { formatMoney } = useFormat();
  const navigate = useNavigate({ from: "/tenants" });
  // 抽屉开合入 URL(?tenant=):告警 / 财务页可直链到某租户抽屉
  const urlTenant = routeApi.useSearch({ select: (s) => s.tenant });
  const setDrilldown = (row: TenantRow | null) =>
    void navigate({
      to: "/tenants",
      replace: true,
      search: (prev) => ({ ...prev, tenant: row?.id, dtab: row ? prev.dtab : undefined }),
    });
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  // 检索落审计,提交才触发:输入框即时值 ↔ URL 的 q(useUrlCommittedInput:防抖回写 + 外部变化同步)
  const urlQ = routeApi.useSearch({ select: (s) => s.q });
  const commitQ = useCallback(
    (next: string | undefined) =>
      void navigate({ to: "/tenants", replace: true, search: (prev) => ({ ...prev, q: next }) }),
    [navigate],
  );
  const { value: input, setValue: setInput } = useUrlCommittedInput(urlQ, commitQ);
  // 状态筛选与注册排序:服务端参数入 URL
  const statusFilter = routeApi.useSearch({ select: (s) => s.tstatus });
  const order = routeApi.useSearch({ select: (s) => s.order });
  const setStatusFilter = (v: string | undefined) =>
    void navigate({
      to: "/tenants",
      replace: true,
      search: (prev) => ({ ...prev, tstatus: v }),
    });
  // 抽屉 Tab 入 URL(?dtab=)
  const dtab = routeApi.useSearch({ select: (s) => s.dtab });
  const onDrawerTabChange = (key: DrawerTab) =>
    void navigate({
      to: "/tenants",
      replace: true,
      search: (prev) => ({ ...prev, dtab: key === "bills" ? undefined : key }),
    });
  // 实名明文查看:必填事由,落审计;readonly 不渲染入口(后端 403)
  const canReveal = role === "ops" || role === "finance" || role === "admin";
  const [revealReason, setRevealReason] = useState<string | null>(null);
  const [revealOpen, setRevealOpen] = useState(false);
  const [reasonInput, setReasonInput] = useState("");
  const tenantsQ = useTenants({
    ...(urlQ ? { q: urlQ } : {}),
    ...(statusFilter ? { status: statusFilter } : {}),
    ...(order ? { order } : {}),
    ...(revealReason !== null ? { reveal: true, reason: revealReason } : {}),
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
  } = tenantsQ;
  const tenants: TenantRow[] = data?.pages.flatMap((p) => p.items) ?? [];
  const drilldown = urlTenant != null ? (tenants.find((r) => r.id === urlTenant) ?? null) : null;
  const freeze = useFreezeTenant();
  const unfreeze = useUnfreezeTenant();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input.Search
          allowClear
          placeholder={tt("tenants.searchPhonePlaceholder")}
          style={{ width: 280 }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onSearch={(v) => {
            // 回车/点按钮立即提交
            setInput(v);
            void navigate({
              to: "/tenants",
              replace: true,
              search: (prev) => ({ ...prev, q: v || undefined }),
            });
          }}
        />
        <Select
          allowClear
          placeholder={tt("common.statusFilter")}
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "active", label: tt("tenants.active") },
            { value: "frozen", label: tt("tenants.frozen") },
          ]}
        />
        {canReveal &&
          (revealReason === null ? (
            <Button size="small" onClick={() => setRevealOpen(true)}>
              {tt("tenants.revealIdName")}
            </Button>
          ) : (
            <Tag color="orange" closable onClose={() => setRevealReason(null)}>
              {tt("tenants.revealActive", { reason: revealReason })}
            </Tag>
          ))}
      </Space>
      <Modal
        title={tt("tenants.revealTitle")}
        open={revealOpen}
        onCancel={() => setRevealOpen(false)}
        okText={tt("tenants.revealConfirm")}
        okButtonProps={{ disabled: reasonInput.trim().length < 2 }}
        onOk={() => {
          setRevealReason(reasonInput.trim());
          setRevealOpen(false);
          setReasonInput("");
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">{tt("tenants.revealHint")}</Typography.Text>
          <Input.TextArea
            rows={2}
            value={reasonInput}
            onChange={(e) => setReasonInput(e.target.value)}
            placeholder={tt("tenants.revealReasonPlaceholder")}
            maxLength={REASON_MAX_LEN}
          />
        </Space>
      </Modal>
      <Table<TenantRow>
        scroll={{ x: 1000 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            >
              {tt("tenants.empty")}
            </TableErrorEmpty>
          ),
        }}
        dataSource={tenants}
        onRow={(r) => ({ style: { cursor: "pointer" }, onClick: () => setDrilldown(r) })}
        onChange={(_p, _f, sorter) => {
          const s = Array.isArray(sorter) ? sorter[0] : sorter;
          if (s?.columnKey !== "created_at") return;
          // ascend → asc;descend 与取消都回默认 desc
          void navigate({
            to: "/tenants",
            replace: true,
            search: (prev) => ({ ...prev, order: s.order === "ascend" ? ("asc" as const) : undefined }),
          });
        }}
        columns={[
          {
            title: "ID",
            dataIndex: "id",
            width: 80,
            fixed: "left",
            render: (v: number) => (
              <span onClick={(e) => e.stopPropagation()}>
                <TenantLink id={v} />
              </span>
            ),
          },
          { title: tt("tenants.colPhone"), dataIndex: "phone_masked", fixed: "left", width: 130 },
          {
            title: tt("tenants.colBalance"),
            dataIndex: "balance",
            render: (v: string) => (
              <span style={{ color: Number(v) <= 0 ? adminColors.negative : undefined }}>{formatMoney(v)}</span>
            ),
          },
          {
            title: tt("tenants.colTotalConsumed"),
            dataIndex: "total_consumed",
            render: (v: string) => formatMoney(v),
          },
          {
            title: tt("tenants.colInstances"),
            dataIndex: "instances",
            width: 70,
          },
          { title: tt("tenants.colDisk"), dataIndex: "disk_gb", render: (v: number) => `${v} GB`, width: 90 },
          {
            title: tt("tenants.colStatus"),
            dataIndex: "status",
            render: (v: string) =>
              v === "active" ? (
                <Tag color="green">{tt("tenants.active")}</Tag>
              ) : (
                <Tag color="red">{tt("tenants.frozen")}</Tag>
              ),
          },
          {
            title: tt("tenants.colCreatedAt"),
            dataIndex: "created_at",
            key: "created_at",
            // 服务端排序仅注册先后;聚合列不提供排序
            sorter: true,
            sortOrder: order === "asc" ? "ascend" : "descend",
            render: formatDateTime,
          },
          {
            title: tt("tenants.colActions"),
            fixed: "right",
            width: 170,
            render: (_, t) => (
              <Space>
                <Button
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDrilldown(t);
                  }}
                >
                  {tt("tenants.viewBilling")}
                </Button>
                {t.status === "active" ? (
                  <span onClick={(e) => e.stopPropagation()}>
                    <ReasonAction
                      label={tt("tenants.freeze")}
                      target={`#${t.id} · ${t.phone_masked}`}
                      danger
                      title={tt("tenants.freezeTitle")}
                      confirmText={tt("tenants.freezeConfirm", {
                        id: t.id,
                        phone: t.phone_masked,
                        count: t.instances,
                      })}
                      disabled={!writable}
                      disabledReason={tt("tenants.noPermission")}
                      onSubmit={async (reason) => {
                        const r = await freeze.mutateAsync({ userId: t.id, data: { reason } });
                        refresh();
                        // 回显后端实停台数
                        return tt("tenants.freezeDone", { count: r.instances_stopped ?? 0 });
                      }}
                    />
                  </span>
                ) : (
                  <span onClick={(e) => e.stopPropagation()}>
                    <ReasonAction
                      label={tt("tenants.unfreeze")}
                      target={`#${t.id} · ${t.phone_masked}`}
                      title={tt("tenants.unfreezeTitle")}
                      confirmText={tt("tenants.unfreezeConfirm", { id: t.id })}
                      disabled={!writable}
                      disabledReason={tt("tenants.noPermission")}
                      onSubmit={async (reason) => {
                        await unfreeze.mutateAsync({ userId: t.id, data: { reason } });
                        refresh();
                      }}
                    />
                  </span>
                )}
              </Space>
            ),
          },
        ]}
      />
      <LoadMore
        hasNextPage={hasNextPage}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={tenants.length}
        onLoadMore={() => void fetchNextPage()}
      />
      <TenantDrawer tenant={drilldown} dtab={dtab} onTabChange={onDrawerTabChange} onClose={() => setDrilldown(null)} />
    </>
  );
}
