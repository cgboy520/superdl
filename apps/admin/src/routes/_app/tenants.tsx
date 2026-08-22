import {
  adminColors,
  formatDateTime,
  instanceStatusMap,
  ledgerTypeMap,
  metaOf,
  skuTierMap,
  type InstanceStatus,
} from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Badge, Button, Card, Drawer, Input, Select, Space, Table, Tabs, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AdminInstanceOut,
  type TenantRow,
  useAdminInstances,
  useForceStop,
  useFreezeTenant,
  useTenantBills,
  useTenantLedger,
  useTenants,
  useUnfreezeTenant,
} from "../../api";
import { useFormat } from "../../lib/format";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "../../components/StatusTag";
import { TenantLink } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/tenants")({
  // q:从其他页的 user_id 链接跳入,按 id 精确找人
  validateSearch: (search: Record<string, unknown>): { q?: string } => ({
    q: typeof search.q === "string" && search.q ? search.q : undefined,
  }),
  component: TenantsPage,
});

function TenantsTab() {
  const { t: tt } = useTranslation();
  const { formatMoney } = useFormat();
  const [drilldown, setDrilldown] = useState<TenantRow | null>(null);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  // 手机号可检索,但列表仍只回掩码:可查不等于可见;纯数字额外按租户 id 精确命中。
  // input=输入框即时值,search=已提交的查询(检索是落审计的敏感读,不能逐键触发)
  const urlQ = Route.useSearch({ select: (s) => s.q });
  const [input, setInput] = useState(urlQ ?? "");
  const [search, setSearch] = useState(urlQ ?? "");
  // URL q 变化(user_id 链接跳入)时同步进输入框:渲染期派生态,不进 effect
  const [prevUrlQ, setPrevUrlQ] = useState(urlQ);
  if (urlQ !== prevUrlQ) {
    setPrevUrlQ(urlQ);
    if (urlQ !== undefined) {
      setInput(urlQ);
      setSearch(urlQ);
    }
  }
  const { data, queryKey } = useTenants(search ? { q: search } : undefined);
  const tenants: TenantRow[] = data ?? [];
  const freeze = useFreezeTenant();
  const unfreeze = useUnfreezeTenant();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <>
    <Input.Search
      allowClear
      placeholder={tt("tenants.searchPhonePlaceholder")}
      style={{ width: 280, marginBottom: 12 }}
      value={input}
      onChange={(e) => {
        setInput(e.target.value);
        if (e.target.value === "") setSearch(""); // 清空即回全量列表
      }}
      onSearch={setSearch}
    />
    <Table<TenantRow>
      scroll={{ x: 1000 }}
      rowKey="id"
      dataSource={tenants}
      onRow={(r) => ({ style: { cursor: "pointer" }, onClick: () => setDrilldown(r) })}
      columns={[
        {
          title: "ID",
          dataIndex: "id",
          width: 80,
          sorter: (a, b) => a.id - b.id,
          render: (v: number) => (
            <span onClick={(e) => e.stopPropagation()}>
              <TenantLink id={v} />
            </span>
          ),
        },
        { title: tt("tenants.colPhone"), dataIndex: "phone_masked" },
        {
          title: tt("tenants.colBalance"),
          dataIndex: "balance",
          sorter: (a, b) => Number(a.balance) - Number(b.balance),
          render: (v: string) => (
            <span style={{ color: Number(v) <= 0 ? adminColors.negative : undefined }}>{formatMoney(v)}</span>
          ),
        },
        {
          title: tt("tenants.colTotalConsumed"),
          dataIndex: "total_consumed",
          sorter: (a, b) => Number(a.total_consumed) - Number(b.total_consumed),
          render: (v: string) => formatMoney(v),
        },
        {
          title: tt("tenants.colInstances"),
          dataIndex: "instances",
          width: 70,
          sorter: (a, b) => a.instances - b.instances,
        },
        { title: tt("tenants.colDisk"), dataIndex: "disk_gb", render: (v: number) => `${v} GB`, width: 90 },
        {
          title: tt("tenants.colStatus"),
          dataIndex: "status",
          filters: [
            { text: tt("tenants.active"), value: "active" },
            { text: tt("tenants.frozen"), value: "frozen" },
          ],
          onFilter: (v, r) => r.status === v,
          render: (v: string) =>
            v === "active" ? <Tag color="green">{tt("tenants.active")}</Tag> : <Tag color="red">{tt("tenants.frozen")}</Tag>,
        },
        {
          title: tt("tenants.colCreatedAt"),
          dataIndex: "created_at",
          render: formatDateTime,
          sorter: (a, b) => a.created_at.localeCompare(b.created_at),
        },
        {
          title: tt("tenants.colActions"),
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
                  // 回显后端实停台数(创建/启动中的由巡检收敛,不在此计数)
                  return tt("tenants.freezeDone", { count: r.instances_stopped ?? 0 });
                }}
              />
              </span>
            ) : (
              <span onClick={(e) => e.stopPropagation()}>
              <ReasonAction
                label={tt("tenants.unfreeze")}
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
    <ListCapNote rows={tenants.length} cap={LIST_CAPS.tenants} />
    <TenantBillingDrawer tenant={drilldown} onClose={() => setDrilldown(null)} />
    </>
  );
}

/** 租户账单下钻:小时账单 + 资金流水,与用户端同源;游标「加载更多」。 */
function TenantBillingDrawer({
  tenant,
  onClose,
}: {
  tenant: TenantRow | null;
  onClose: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney, formatHourlyPrice, formatDuration } = useFormat();
  const ledger = useTenantLedger(tenant?.id ?? null);
  const bills = useTenantBills(tenant?.id ?? null);
  const ledgerRows = ledger.data?.pages.flatMap((p) => p.items) ?? [];
  const billRows = bills.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <Drawer
      width={880}
      open={tenant !== null}
      onClose={onClose}
      title={
        tenant
          ? t("tenants.drawerTitle", { id: tenant.id, phone: tenant.phone_masked })
          : undefined
      }
    >
      {tenant && (
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Space size={24}>
            <span>
              {t("tenants.colBalance")}:<b>{formatMoney(tenant.balance)}</b>
            </span>
            <span>
              {t("tenants.colTotalConsumed")}:<b>{formatMoney(tenant.total_consumed)}</b>
            </span>
            <span>
              {t("tenants.colInstances")}:<b>{tenant.instances}</b>
            </span>
          </Space>
          <Tabs
            items={[
              {
                key: "bills",
                label: t("tenants.tabBills"),
                children: (
                  <>
                  <Table
                    size="small"
                    rowKey="id"
                    loading={bills.isLoading}
                    pagination={false}
                    scroll={{ y: 420 }}
                    dataSource={billRows}
                    columns={[
                      {
                        title: t("tenants.colHour"),
                        dataIndex: "hour_start",
                        render: formatDateTime,
                      },
                      { title: t("tenants.colInstanceId"), dataIndex: "instance_id", width: 90 },
                      {
                        title: t("tenants.colSeconds"),
                        dataIndex: "seconds_used",
                        render: (v: number) => formatDuration(v),
                      },
                      {
                        title: t("tenants.colUnitPrice"),
                        dataIndex: "unit_price",
                        render: (v: string) => formatHourlyPrice(v),
                      },
                      {
                        title: t("tenants.colAmount"),
                        dataIndex: "amount",
                        render: (v: string) => formatMoney(v),
                      },
                    ]}
                  />
                  {bills.hasNextPage && (
                    <Button
                      block
                      size="small"
                      style={{ marginTop: 8 }}
                      loading={bills.isFetchingNextPage}
                      onClick={() => void bills.fetchNextPage()}
                    >
                      {t("common.loadMore")}
                    </Button>
                  )}
                  </>
                ),
              },
              {
                key: "ledger",
                label: t("tenants.tabLedger"),
                children: (
                  <>
                  <Table
                    size="small"
                    rowKey="id"
                    loading={ledger.isLoading}
                    pagination={false}
                    scroll={{ y: 420 }}
                    dataSource={ledgerRows}
                    columns={[
                      {
                        title: t("tenants.colTime"),
                        dataIndex: "created_at",
                        render: formatDateTime,
                      },
                      {
                        title: t("tenants.colType"),
                        dataIndex: "type",
                        width: 90,
                        render: (v: string) => {
                          const m = metaOf(ledgerTypeMap, v);
                          return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
                        },
                      },
                      {
                        title: t("tenants.colAmount"),
                        dataIndex: "amount",
                        render: (v: string) => (
                          <span style={{ color: v.startsWith("-") ? undefined : adminColors.positive }}>
                            {formatMoney(v)}
                          </span>
                        ),
                      },
                      {
                        title: t("tenants.colBalanceAfter"),
                        dataIndex: "balance_after",
                        render: (v: string) => formatMoney(v),
                      },
                      { title: t("tenants.colRemark"), dataIndex: "remark" },
                    ]}
                  />
                  {ledger.hasNextPage && (
                    <Button
                      block
                      size="small"
                      style={{ marginTop: 8 }}
                      loading={ledger.isFetchingNextPage}
                      onClick={() => void ledger.fetchNextPage()}
                    >
                      {t("common.loadMore")}
                    </Button>
                  )}
                  </>
                ),
              },
            ]}
          />
        </Space>
      )}
    </Drawer>
  );
}

function InstancesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [status, setStatus] = useState<string | undefined>();
  const [search, setSearch] = useState("");
  const [nodeName, setNodeName] = useState("");
  const qc = useQueryClient();
  const { data: instances, queryKey } = useAdminInstances({
    ...(status ? { status } : {}),
    ...(search ? { q: search } : {}),
    ...(nodeName ? { node_name: nodeName } : {}),
  });
  const forceStop = useForceStop();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("tenants.statusFilter")}
          style={{ width: 160 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(instanceStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <Input.Search
          allowClear
          placeholder={t("tenants.searchInstancePlaceholder")}
          style={{ width: 220 }}
          onSearch={setSearch}
        />
        <Input.Search
          allowClear
          placeholder={t("tenants.searchNodePlaceholder")}
          style={{ width: 200 }}
          onSearch={setNodeName}
        />
      </Space>
      <Table<AdminInstanceOut>
        scroll={{ x: 1000 }}
        rowKey="uuid"
        dataSource={instances ?? []}
        columns={[
          { title: t("tenants.colInstance"), dataIndex: "name" },
          {
            title: t("tenants.colOwner"),
            dataIndex: "user_id",
            width: 90,
            render: (v: number) => <TenantLink id={v} />,
          },
          {
            title: t("tenants.colNode"),
            dataIndex: "node_name",
            width: 160,
            render: (v: string | null) => v ?? "—",
          },
          {
            title: t("tenants.colStatus"),
            dataIndex: "status",
            render: (v: InstanceStatus) => {
              const m = metaOf(instanceStatusMap, v);
              return <Badge color={m?.color} text={m ? t(m.labelKey) : v} />;
            },
          },
          {
            title: t("tenants.colSpec"),
            render: (_, r) => {
              const tm = metaOf(skuTierMap, r.spec.tier as string);
              return (
                <Space>
                  <span>
                    {String(r.spec.gpu_model)} × {r.gpu_count}
                  </span>
                  <StatusTag color={tm?.color}>{tm ? t(tm.labelKey) : String(r.spec.tier)}</StatusTag>
                </Space>
              );
            },
          },
          { title: t("tenants.colSshPort"), dataIndex: "ssh_port", width: 100 },
          { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.colActions"),
            render: (_, r) => {
              return (
                <Space>
                  <ReasonAction
                    label={t("tenants.forceStop")}
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
                  <Tooltip title={t("tenants.evictP1")}>
                    <Button size="small" disabled>
                      {t("tenants.evict")}
                    </Button>
                  </Tooltip>
                </Space>
              );
            },
          },
        ]}
      />
      <ListCapNote rows={(instances ?? []).length} cap={LIST_CAPS.instances} />
    </>
  );
}

function TenantsPage() {
  const { t } = useTranslation();
  return (
    <Card title={t("menu.tenants")}>
      <Tabs
        items={[
          { key: "tenants", label: t("tenants.tabTenants"), children: <TenantsTab /> },
          { key: "instances", label: t("tenants.tabInstances"), children: <InstancesTab /> },
        ]}
      />
    </Card>
  );
}
