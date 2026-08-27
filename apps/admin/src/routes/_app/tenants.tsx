import {
  adminColors,
  deletionStatusMap,
  formatDateTime,
  instanceStatusMap,
  metaOf,
  skuTierMap,
  skuVariant,
  type InstanceStatus,
} from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { App, Badge, Button, Card, Input, Modal, Select, Space, Table, Tabs, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AdminInstanceOut,
  type DeletionRow,
  type TenantRow,
  useAdminInstances,
  useApproveDeletion,
  useDeletionRequests,
  useForceStop,
  useFreezeTenant,
  useRejectDeletion,
  useTenants,
  useUnfreezeTenant,
} from "../../api";
import { useApiErrorText } from "../../lib/apiError";
import { useFormat } from "../../lib/format";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { LoadMoreButton } from "../../components/LoadMore";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "../../components/StatusTag";
import { TenantLink, tenantColumn } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { TenantDrawer } from "./-TenantDrawer";

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
  const { data, queryKey, hasNextPage, isFetchingNextPage, fetchNextPage } = useTenants(
    search ? { q: search } : undefined,
  );
  const tenants: TenantRow[] = data?.pages.flatMap((p) => p.items) ?? [];
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
    <LoadMoreButton
      visible={Boolean(hasNextPage)}
      loading={isFetchingNextPage}
      onClick={() => void fetchNextPage()}
    />
    <TenantDrawer tenant={drilldown} onClose={() => setDrilldown(null)} />
    </>
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
  const { data, queryKey, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useAdminInstances({
      ...(status ? { status } : {}),
      ...(search ? { q: search } : {}),
      ...(nodeName ? { node_name: nodeName } : {}),
    });
  const instances: AdminInstanceOut[] = data?.pages.flatMap((p) => p.items) ?? [];
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
        loading={isLoading}
        dataSource={instances}
        columns={[
          { title: t("tenants.colInstance"), dataIndex: "name" },
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
            render: (v: InstanceStatus) => {
              const m = metaOf(instanceStatusMap, v);
              return <Badge color={m?.color} text={m ? t(m.labelKey) : v} />;
            },
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
      <LoadMoreButton
        visible={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        onClick={() => void fetchNextPage()}
      />
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
          { key: "deletions", label: t("tenants.tabDeletions"), children: <DeletionsTab /> },
        ]}
      />
    </Card>
  );
}

/** 注销申请:列表 + 处理。执行仅超管;确认弹窗列出校验计数,全 0 且过冷静期才可点。 */
function DeletionsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { message } = App.useApp();
  const errText = useApiErrorText();
  const { formatMoney, formatCountdown } = useFormat();
  const role = useAdminRole();
  const isAdmin = role === "admin";
  const [status, setStatus] = useState<string | undefined>();
  const qc = useQueryClient();
  const { data, queryKey } = useDeletionRequests(status ? { status } : undefined);
  const rows: DeletionRow[] = data ?? [];
  const approve = useApproveDeletion();
  const reject = useRejectDeletion();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const [approving, setApproving] = useState<DeletionRow | null>(null);
  const [approveLoading, setApproveLoading] = useState(false);
  // 渲染期禁调 Date.now(eslint react-hooks/purity):挂载快照即可,服务端仍会二次校验冷静期
  const [nowTs] = useState(() => Date.now());

  const runApprove = async () => {
    if (!approving) return;
    setApproveLoading(true);
    try {
      await approve.mutateAsync({ requestId: approving.id });
      message.success(t("tenants.deletion.executed"));
      setApproving(null);
    } catch (e) {
      // 校验不过 → 409(申请已被自动驳回);冷静期未满 → 409
      message.error(errText(e, t("common.actionFailed", { action: t("tenants.deletion.approveTitle") })));
    } finally {
      setApproveLoading(false);
      refresh();
    }
  };

  const cooldownLeft = (r: DeletionRow) =>
    r.status === "pending" ? formatCountdown(r.cooldown_ends_at) : null;
  const precheckClear = (r: DeletionRow) =>
    r.instances_active === 0 && r.disks_active === 0 && Number(r.balance) === 0;
  const cooldownOver = (r: DeletionRow) => new Date(r.cooldown_ends_at).getTime() <= nowTs;

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("tenants.deletion.statusFilter")}
          style={{ width: 160 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(deletionStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
      </Space>
      <Table<DeletionRow>
        scroll={{ x: 1100 }}
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: "ID", dataIndex: "id", width: 70 },
          {
            title: t("tenants.deletion.colUser"),
            render: (_, r) => (
              <Space size={8}>
                <TenantLink id={r.user_id} />
                <span>{r.phone_masked}</span>
              </Space>
            ),
          },
          {
            title: t("tenants.deletion.colStatus"),
            dataIndex: "status",
            render: (v: string) => {
              const m = metaOf(deletionStatusMap, v);
              return <Badge color={m?.color} text={m ? t(m.labelKey) : v} />;
            },
          },
          { title: t("tenants.deletion.colReason"), dataIndex: "reason", ellipsis: true },
          {
            title: t("tenants.deletion.colRequestedAt"),
            dataIndex: "requested_at",
            render: formatDateTime,
          },
          {
            title: t("tenants.deletion.colCooldownEnd"),
            dataIndex: "cooldown_ends_at",
            render: (v: string, r) => (
              <Space size={8}>
                <span>{formatDateTime(v)}</span>
                {r.status === "pending" && !cooldownOver(r) && (
                  <Tag color="orange">{cooldownLeft(r)}</Tag>
                )}
              </Space>
            ),
          },
          {
            title: t("tenants.deletion.colPrecheck"),
            render: (_, r) => (
              <Space size={8}>
                <span>{t("tenants.deletion.precheckInstances", { count: r.instances_active })}</span>
                <span>{t("tenants.deletion.precheckDisks", { count: r.disks_active })}</span>
                <span>{formatMoney(r.balance)}</span>
              </Space>
            ),
          },
          {
            title: t("tenants.deletion.colProcessed"),
            render: (_, r) =>
              r.processed_at ? (
                <Space orientation="vertical" size={0}>
                  <Typography.Text style={{ fontSize: 12 }}>
                    {t("tenants.deletion.processedBy", {
                      id: r.processed_by ?? "-",
                      time: formatDateTime(r.processed_at),
                    })}
                  </Typography.Text>
                  {r.note && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {r.note}
                    </Typography.Text>
                  )}
                </Space>
              ) : (
                "—"
              ),
          },
          {
            title: t("tenants.colActions"),
            render: (_, r) =>
              r.status === "pending" ? (
                <Space>
                  <Tooltip title={isAdmin ? undefined : t("tenants.deletion.noPermission")}>
                    <Button
                      size="small"
                      danger
                      disabled={!isAdmin}
                      onClick={() => setApproving(r)}
                    >
                      {t("tenants.deletion.approve")}
                    </Button>
                  </Tooltip>
                  <ReasonAction
                    label={t("tenants.deletion.reject")}
                    title={t("tenants.deletion.rejectTitle")}
                    confirmText={t("tenants.deletion.rejectConfirm")}
                    disabled={!isAdmin}
                    disabledReason={t("tenants.deletion.noPermission")}
                    onSubmit={async (note) => {
                      await reject.mutateAsync({ requestId: r.id, data: { note } });
                      refresh();
                    }}
                  />
                </Space>
              ) : null,
          },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.deletions} />

      <Modal
        title={t("tenants.deletion.approveTitle")}
        open={approving !== null}
        onCancel={() => setApproving(null)}
        okText={t("tenants.deletion.approve")}
        okButtonProps={{
          danger: true,
          loading: approveLoading,
          disabled: !approving || !precheckClear(approving) || !cooldownOver(approving),
        }}
        onOk={() => void runApprove()}
      >
        {approving && (
          <Space orientation="vertical" size={8}>
            <Typography.Text>
              {t("tenants.deletion.approveCheckLine", {
                instances: approving.instances_active,
                disks: approving.disks_active,
                balance: formatMoney(approving.balance),
              })}
            </Typography.Text>
            {!precheckClear(approving) && (
              <Typography.Text type="danger">
                {t("tenants.deletion.approveBlocked")}
              </Typography.Text>
            )}
            {!cooldownOver(approving) && (
              <Typography.Text type="warning">
                {t("tenants.deletion.cooldownRemaining", {
                  countdown: cooldownLeft(approving) ?? "",
                })}
              </Typography.Text>
            )}
            <Typography.Text type="secondary">
              {t("tenants.deletion.approveConfirmText")}
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </>
  );
}
