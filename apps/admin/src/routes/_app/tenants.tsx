import {
  adminColors,
  deletionStatusMap,
  fontSize,
  formatDateTime,
  instanceStatusMap,
  marketLabelKey,
  marketMap,
  metaOf,
  skuTierMap,
  skuVariant,
  useDebouncedValue,
  useNow,
  workloadTypeMap,
  type InstanceStatus,
} from "@superdl/ui";
import { HexTag, LoadMore, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { App, Badge, Button, Card, Input, Modal, Select, Space, Table, Tabs, Tag, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AdminInstanceOut,
  type DeletionRow,
  type TenantRow,
  isApiError,
  useAdminInstances,
  useApproveDeletion,
  useDeletionRequests,
  useForceStop,
  useFreezeTenant,
  usePreemptInstance,
  useRejectDeletion,
  useTenants,
  useUnfreezeTenant,
} from "../../api";
import { useApiErrorText, useFormat } from "@superdl/ui";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { ReasonAction } from "../../components/ReasonAction";
import { TenantLink, tenantColumn } from "../../components/TenantLink";
import { REASON_MAX_LEN } from "../../lib/validators";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { DRAWER_TABS, type DrawerTab, TenantDrawer } from "./-TenantDrawer";

const TENANTS_TABS = ["tenants", "instances", "deletions"] as const;
type TenantsTab = (typeof TENANTS_TABS)[number];

export const Route = createFileRoute("/_app/tenants")({
  // q:从其他页的 user_id 链接跳入,按 id 精确找人;tab/dtab:页内与抽屉 Tab 入 URL;
  // istatus/inode/iq:全局实例 Tab 的服务端筛选与实例名检索入 URL;tstatus:租户状态筛选;order:注册先后排序
  validateSearch: (search: Record<string, unknown>): {
    q?: string;
    tab?: TenantsTab;
    dtab?: DrawerTab;
    istatus?: string;
    inode?: string;
    iq?: string;
    tstatus?: string;
    order?: "asc";
  } => ({
    q: typeof search.q === "string" && search.q ? search.q : undefined,
    tab: TENANTS_TABS.includes(search.tab as TenantsTab) ? (search.tab as TenantsTab) : undefined,
    dtab: DRAWER_TABS.includes(search.dtab as DrawerTab) ? (search.dtab as DrawerTab) : undefined,
    istatus:
      typeof search.istatus === "string" && search.istatus in instanceStatusMap
        ? search.istatus
        : undefined,
    inode: typeof search.inode === "string" && search.inode ? search.inode : undefined,
    iq: typeof search.iq === "string" && search.iq ? search.iq : undefined,
    tstatus:
      search.tstatus === "active" || search.tstatus === "frozen" ? search.tstatus : undefined,
    order: search.order === "asc" ? "asc" : undefined,
  }),
  component: TenantsPage,
});

function TenantsTab() {
  const { t: tt } = useTranslation();
  const { formatMoney } = useFormat();
  const navigate = useNavigate({ from: "/tenants" });
  const [drilldown, setDrilldown] = useState<TenantRow | null>(null);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  // 手机号可检索但列表仍只回掩码;纯数字额外按租户 id 精确命中。
  // 检索是落审计的敏感读,必须提交才触发:input=输入框即时值,已提交的查询 = URL 的 q
  const urlQ = Route.useSearch({ select: (s) => s.q });
  const [input, setInput] = useState(urlQ ?? "");
  // URL q 变化(user_id 链接跳入)时同步进输入框:渲染期派生态,不进 effect
  const [prevUrlQ, setPrevUrlQ] = useState(urlQ);
  if (urlQ !== prevUrlQ) {
    setPrevUrlQ(urlQ);
    if (urlQ !== undefined) {
      setInput(urlQ);
    }
  }
  // 输入 300ms 防抖回写 ?q=(双向同步:控件→URL,共享 useDebouncedValue);防抖期间不触发查询,
  // 已提交查询态以 URL 为唯一事实源(useTenants 直接读 urlQ,不再有中间 state)
  const debouncedInput = useDebouncedValue(input, 300);
  useEffect(() => {
    // 与 URL 已一致(初载/深链跳入/回车立即提交后的回声)不再回写,避免多余导航
    if (debouncedInput === (urlQ ?? "")) return;
    void navigate({
      to: "/tenants",
      replace: true,
      search: (prev) => ({ ...prev, q: debouncedInput || undefined }),
    });
  }, [debouncedInput, urlQ, navigate]);
  // 状态筛选与注册排序:服务端参数入 URL(游标分页下客户端 filters/sorter 只作用于已加载页,是假筛选/假排序)
  const statusFilter = Route.useSearch({ select: (s) => s.tstatus });
  const order = Route.useSearch({ select: (s) => s.order });
  const setStatusFilter = (v: string | undefined) =>
    void navigate({
      to: "/tenants",
      replace: true,
      search: (prev) => ({ ...prev, tstatus: v }),
    });
  // 抽屉 Tab 入 URL(?dtab=):刷新/分享后回到同一子页
  const dtab = Route.useSearch({ select: (s) => s.dtab });
  const onDrawerTabChange = (key: DrawerTab) =>
    void navigate({
      to: "/tenants",
      replace: true,
      search: (prev) => ({ ...prev, dtab: key === "bills" ? undefined : key }),
    });
  // 实名明文查看(全角色默认脱敏):逐次显式动作,必填事由,每次明文读落审计;
  // readonly 不可 reveal(后端 403,这里直接不渲染入口)
  const canReveal = role === "ops" || role === "finance" || role === "admin";
  const [revealReason, setRevealReason] = useState<string | null>(null);
  const [revealOpen, setRevealOpen] = useState(false);
  const [reasonInput, setReasonInput] = useState("");
  const tenantsQ = useTenants(
    {
      ...(urlQ ? { q: urlQ } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(order ? { order } : {}),
      ...(revealReason !== null ? { reveal: true, reason: revealReason } : {}),
    },
  );
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = tenantsQ;
  const tenants: TenantRow[] = data?.pages.flatMap((p) => p.items) ?? [];
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
        // 回车/点按钮立即提交(不等防抖),与防抖回写同一条路径(随后的防抖回声因与 URL 一致被跳过)
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
        // ascend → asc;descend 与取消排序都回默认 desc(默认值剥离出 URL)
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
            v === "active" ? <Tag color="green">{tt("tenants.active")}</Tag> : <Tag color="red">{tt("tenants.frozen")}</Tag>,
        },
        {
          title: tt("tenants.colCreatedAt"),
          dataIndex: "created_at",
          key: "created_at",
          // 服务端排序(注册先后 = id 单调):唯一与游标分页兼容的排序键;
          // 余额/消费等聚合列按页拼装,不提供排序(假排序比没有更糟)
          sorter: true,
          sortOrder: order === "asc" ? "ascend" : "descend",
          render: formatDateTime,
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
    <LoadMore
      hasNextPage={Boolean(hasNextPage)}
      loading={isFetchingNextPage}
      isError={isFetchNextPageError}
      loadedCount={tenants.length}
      onLoadMore={() => void fetchNextPage()}
    />
    <TenantDrawer tenant={drilldown} dtab={dtab} onTabChange={onDrawerTabChange} onClose={() => setDrilldown(null)} />
    </>
  );
}

function InstancesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/tenants" });
  const role = useAdminRole();
  const writable = canWriteOps(role);
  // status/node_name/实例名检索全部入 URL(commit 制);输入框经渲染期派生回流
  const status = Route.useSearch({ select: (s) => s.istatus });
  const nodeName = Route.useSearch({ select: (s) => s.inode });
  const instQ = Route.useSearch({ select: (s) => s.iq });
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
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = instancesQ;
  const instances: AdminInstanceOut[] = data?.pages.flatMap((p) => p.items) ?? [];
  const forceStop = useForceStop();
  const preempt = usePreemptInstance();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: 160 }}
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
          style={{ width: 220 }}
          value={instInput}
          onChange={(e) => setInstInput(e.target.value)}
          onSearch={(v) => setUrl({ iq: v || undefined })}
        />
        <Input.Search
          allowClear
          placeholder={t("tenants.searchNodePlaceholder")}
          style={{ width: 200 }}
          value={nodeInput}
          onChange={(e) => setNodeInput(e.target.value)}
          onSearch={(v) => setUrl({ inode: v || undefined })}
        />
      </Space>
      <Table<AdminInstanceOut>
        scroll={{ x: 1250 }}
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
                  <HexTag color={tm?.color}>{tm ? t(tm.labelKey) : String(r.spec.tier)}</HexTag>
                </Space>
              );
            },
          },
          {
            // 形态:开发机 / 在线服务;服务行链到在线服务页按 slug 找服务
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
            // 购买模式:标签取 packages/ui 的同一份映射,不在管理端另拼一遍
            title: t("tenants.colMarket"),
            dataIndex: "market",
            width: 110,
            render: (v: string, r) => {
              const labelKey = marketLabelKey(v, r.subscription?.period);
              return (
                <HexTag color={metaOf(marketMap, v)?.color}>
                  {labelKey ? t(labelKey) : v}
                </HexTag>
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
                  {/* 强制回收:腾容量用,走与自动抢占同一条路径(通知 + 宽限窗,不是立即删 Pod)。
                      必须与强制停止分成两个按钮:合并会让「被处置」与「被回收」在时间线上分不开 */}
                  <ReasonAction
                    label={t("tenants.preempt")}
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
        hasNextPage={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={instances.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </>
  );
}

function TenantsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/tenants" });
  const tab = Route.useSearch({ select: (s) => s.tab });
  return (
    <PageContainer title={t("menu.tenants")}>
      <Card>
        <Tabs
          activeKey={tab ?? "tenants"}
          onChange={(key) =>
            void navigate({
              to: "/tenants",
              replace: true,
              search: (prev) => ({ ...prev, tab: key === "tenants" ? undefined : (key as TenantsTab) }),
            })
          }
          items={[
            { key: "tenants", label: t("tenants.tabTenants"), children: <TenantsTab /> },
            { key: "instances", label: t("tenants.tabInstances"), children: <InstancesTab /> },
            { key: "deletions", label: t("tenants.tabDeletions"), children: <DeletionsTab /> },
          ]}
        />
      </Card>
    </PageContainer>
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
  const { data, queryKey, isLoading, isError, error, refetch } = useDeletionRequests(status ? { status } : undefined);
  const rows: DeletionRow[] = data ?? [];
  const approve = useApproveDeletion();
  const reject = useRejectDeletion();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const [approving, setApproving] = useState<DeletionRow | null>(null);
  const [approveLoading, setApproveLoading] = useState(false);
  // 冷静期倒计时按 30s tick 刷新(挂载快照会随页面长开而过期,按钮解禁/倒计时都需要活的时间)
  const nowTs = useNow(30_000);

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
                  <Typography.Text style={{ fontSize: fontSize.caption }}>
                    {t("tenants.deletion.processedBy", {
                      id: r.processed_by ?? "-",
                      time: formatDateTime(r.processed_at),
                    })}
                  </Typography.Text>
                  {r.note && (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
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
