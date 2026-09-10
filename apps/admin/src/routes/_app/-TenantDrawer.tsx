/** 租户下钻抽屉:实名摘要 + 账单/流水/订单/实例/在线服务/配额/事件 七 Tab + 跳审计。
 *  文件名 dash 前缀 = 非路由组件,不进 TanStack Router 的路由树。
 *  抽屉数据全部按 user_id / uuid 反查,实例选择器在账单过滤与事件时间线间复用同一份列表。 */

import {
  fontSize,
  formatDate,
  formatDateTime,
  instanceStatusMap,
  ledgerTypeMap,
  marketLabelKey,
  marketMap,
  metaOf,
  subscriptionStatusMap,
} from "@superdl/ui";
import { DataErrorAlert, HexTag, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  App,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Skeleton,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AdminInstanceOut,
  type InstanceEvent,
  type OrderRow,
  type TenantRow,
  exportTenantLedgerCsv,
  isApiError,
  useAdminInstances,
  useInstanceEvents,
  useOrders,
  useSetTenantQuota,
  useTenantBills,
  useTenantLedger,
  useTenantQuota,
} from "../../api";
import { useOrderColumns } from "../../components/orderColumns";
import { SignedAmount } from "../../components/SignedAmount";
import { useApiErrorText } from "@superdl/ui";
import { useCsvExport } from "@superdl/ui";
import { useFormat } from "@superdl/ui";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { AdminServicesTable } from "./-AdminServicesTable";

// 抽屉 Tab 白名单(tenants 路由 ?dtab= 校验共用)
export const DRAWER_TABS = [
  "bills",
  "ledger",
  "orders",
  "instances",
  "services",
  "quota",
  "events",
] as const;
export type DrawerTab = (typeof DRAWER_TABS)[number];

export function TenantDrawer({
  tenant,
  dtab,
  onTabChange,
  onClose,
}: {
  tenant: TenantRow | null;
  /** 抽屉 Tab 受控于调用方 URL 参数(?dtab=) */
  dtab?: DrawerTab;
  onTabChange?: (tab: DrawerTab) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  // 抽屉级实例列表:账单过滤 / 事件选择器 / 实例 Tab 三处复用(关抽屉不拉取;取前 100 条)
  const tenantInstances = useAdminInstances(
    tenant ? { user_id: tenant.id } : undefined,
    { enabled: tenant !== null, limit: 100 },
  );
  const instances = tenantInstances.data?.pages.flatMap((p) => p.items) ?? [];
  // 租户实例精确计数(后端 user_id 过滤时附):「正好 100 条」与「被 100 条上限截断」必须分得开
  const instancesTotal = tenantInstances.data?.pages[0]?.total ?? null;

  return (
    <Drawer
      // 响应式宽度:桌面 880,窄屏吃满视口宽(antd Drawer 移动端正解)
      width="min(880px, 100vw)"
      open={tenant !== null}
      onClose={onClose}
      title={
        tenant
          ? t("tenants.drawerTitle", { id: tenant.id, phone: tenant.phone_masked })
          : undefined
      }
      extra={
        tenant && (
          <Link to="/audit" search={{ actor_type: "user", actor_id: String(tenant.id) }}>
            {t("tenants.gotoAudit")}
          </Link>
        )
      }
    >
      {tenant && (
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Space size={24} wrap>
            <span>
              {t("tenants.colBalance")}:<b>{formatMoney(tenant.balance)}</b>
            </span>
            <span>
              {t("tenants.colTotalConsumed")}:<b>{formatMoney(tenant.total_consumed)}</b>
            </span>
            <span>
              {t("tenants.colInstances")}:<b>{tenant.instances}</b>
            </span>
            <span>
              {t("tenants.realname")}:
              {tenant.verification_status === "verified" ? (
                <Tag color="green">{t("tenants.realnameVerified")}</Tag>
              ) : (
                <Tag>{t("tenants.realnameUnverified")}</Tag>
              )}
            </span>
            {tenant.id_name && (
              <span>
                {t("tenants.colIdName")}:<b>{tenant.id_name}</b>
              </span>
            )}
          </Space>
          <Tabs
            activeKey={dtab ?? "bills"}
            onChange={(key) => onTabChange?.(key as DrawerTab)}
            items={[
              {
                key: "bills",
                label: t("tenants.tabBills"),
                children: <BillsTab userId={tenant.id} instances={instances} />,
              },
              {
                key: "ledger",
                label: t("tenants.tabLedger"),
                children: <LedgerTab userId={tenant.id} />,
              },
              {
                key: "orders",
                label: t("tenants.tabOrders"),
                children: <OrdersTab userId={tenant.id} />,
              },
              {
                key: "instances",
                label: t("tenants.tabInstances"),
                children: <TenantInstancesTab instances={instances} total={instancesTotal} />,
              },
              {
                key: "services",
                label: t("tenants.tabServices"),
                children: <AdminServicesTable userId={tenant.id} compact />,
              },
              {
                key: "quota",
                label: t("tenants.tabQuota"),
                children: <QuotaTab userId={tenant.id} />,
              },
              {
                key: "events",
                label: t("tenants.tabEvents"),
                children: <EventsTab instances={instances} />,
              },
            ]}
          />
        </Space>
      )}
    </Drawer>
  );
}

/** 小时账单:可按实例过滤(排障:只盯一台机的账);游标「加载更多」。 */
function BillsTab({ userId, instances }: { userId: number; instances: AdminInstanceOut[] }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney, formatHourlyPrice, formatDuration } = useFormat();
  const [instanceId, setInstanceId] = useState<number | null>(null);
  const bills = useTenantBills(userId, instanceId);
  const billRows = bills.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder={t("tenants.billsInstanceFilter")}
        style={{ width: 280, marginBottom: 12 }}
        value={instanceId}
        onChange={(v: number | undefined) => setInstanceId(v ?? null)}
        options={instances.map((i) => ({
          value: i.id,
          label: `${i.name} (${i.uuid.slice(0, 8)})`,
        }))}
      />
      <Table
        size="small"
        rowKey="id"
        loading={bills.isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={bills.isError}
              isForbidden={isApiError(bills.error) && bills.error.status === 403}
              onRetry={() => void bills.refetch()}
            />
          ),
        }}
        pagination={false}
        scroll={{ y: 420 }}
        dataSource={billRows}
        columns={[
          { title: t("tenants.colHour"), dataIndex: "hour_start", render: formatDateTime },
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
          { title: t("tenants.colAmount"), dataIndex: "amount", render: (v: string) => formatMoney(v) },
        ]}
      />
      <LoadMore
        hasNextPage={Boolean(bills.hasNextPage)}
        loading={bills.isFetchingNextPage}
        isError={bills.isFetchNextPageError}
        loadedCount={billRows.length}
        onLoadMore={() => void bills.fetchNextPage()}
      />
    </>
  );
}

function LedgerTab({ userId }: { userId: number }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const ledger = useTenantLedger(userId);
  const ledgerRows = ledger.data?.pages.flatMap((p) => p.items) ?? [];
  const { doExport, exporting } = useCsvExport((tz, lang) => exportTenantLedgerCsv(userId, tz, lang));

  return (
    <>
      <div style={{ marginBottom: 8, textAlign: "right" }}>
        <Button size="small" onClick={() => void doExport()} loading={exporting}>
          {t("common.exportCsv")}
        </Button>
      </div>
      <Table
        size="small"
        rowKey="id"
        loading={ledger.isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={ledger.isError}
              isForbidden={isApiError(ledger.error) && ledger.error.status === 403}
              onRetry={() => void ledger.refetch()}
            />
          ),
        }}
        pagination={false}
        scroll={{ y: 420 }}
        dataSource={ledgerRows}
        columns={[
          { title: t("tenants.colTime"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.colType"),
            dataIndex: "type",
            width: 90,
            render: (v: string) => {
              const m = metaOf(ledgerTypeMap, v);
              return <HexTag color={m?.color}>{m ? t(m.labelKey) : v}</HexTag>;
            },
          },
          {
            title: t("tenants.colAmount"),
            dataIndex: "amount",
            render: (v: string) => <SignedAmount value={v} highlightNegative={false} />,
          },
          {
            title: t("tenants.colBalanceAfter"),
            dataIndex: "balance_after",
            render: (v: string) => formatMoney(v),
          },
          { title: t("tenants.colRemark"), dataIndex: "remark", ellipsis: true },
        ]}
      />
      <LoadMore
        hasNextPage={Boolean(ledger.hasNextPage)}
        loading={ledger.isFetchingNextPage}
        isError={ledger.isFetchNextPageError}
        loadedCount={ledgerRows.length}
        onLoadMore={() => void ledger.fetchNextPage()}
      />
    </>
  );
}

/** 订单反查:该租户的充值订单(游标分页,加载更多)。 */
function OrdersTab({ userId }: { userId: number }) {
  const orders = useOrders({ user_id: userId });
  const rows: OrderRow[] = orders.data?.pages.flatMap((p) => p.items) ?? [];
  const columns = useOrderColumns({ withTenant: false });

  return (
    <>
      <Table<OrderRow>
        size="small"
        rowKey="order_no"
        loading={orders.isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={orders.isError}
              isForbidden={isApiError(orders.error) && orders.error.status === 403}
              onRetry={() => void orders.refetch()}
            />
          ),
        }}
        pagination={false}
        scroll={{ y: 420 }}
        dataSource={rows}
        columns={columns}
      />
      <LoadMore
        hasNextPage={Boolean(orders.hasNextPage)}
        loading={orders.isFetchingNextPage}
        isError={orders.isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void orders.fetchNextPage()}
      />
    </>
  );
}

/** 实例反查:只读视图(写操作集中在「全局实例」Tab,口径单一;抽屉取前 100 条)。 */
function TenantInstancesTab({
  instances,
  total,
}: {
  instances: AdminInstanceOut[];
  total: number | null;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  return (
    <>
      {/* 截断必明示:超过 100 台时账单过滤/事件选择器同样只能选到前 100 台 */}
      {total !== null && total > instances.length && (
        <Typography.Text
          type="warning"
          style={{ display: "block", marginBottom: 8, fontSize: fontSize.caption }}
        >
          {t("tenants.instancesCapped", { shown: instances.length, total })}
        </Typography.Text>
      )}
      <Table<AdminInstanceOut>
      size="small"
      rowKey="uuid"
      pagination={false}
      scroll={{ x: 840, y: 420 }}
      dataSource={instances}
      columns={[
        {
          title: t("tenants.colInstance"),
          render: (_, r) => (
            <Space size={8}>
              <span>{r.name}</span>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {r.uuid.slice(0, 8)}
              </Typography.Text>
            </Space>
          ),
        },
        {
          title: t("tenants.colStatus"),
          dataIndex: "status",
          width: 110,
          render: (v: string) => {
            const m = metaOf(instanceStatusMap, v);
            return <HexTag color={m?.color}>{m ? t(m.labelKey) : v}</HexTag>;
          },
        },
        {
          // 购买模式:标签取 packages/ui 的同一份映射,不在管理端另拼一遍;到期信息已内联在
          // subscription 里(按量与已释放实例为 null),不逐行再打接口。在保给到期日,失效给状态词并上色
          title: t("tenants.colMarket"),
          width: 150,
          render: (_, r) => {
            const labelKey = marketLabelKey(r.market, r.subscription?.period);
            const sub = r.subscription;
            const subMeta = sub ? metaOf(subscriptionStatusMap, sub.status) : undefined;
            const lapsed = sub != null && sub.status !== "active";
            return (
              <Space orientation="vertical" size={0}>
                <HexTag color={metaOf(marketMap, r.market)?.color}>
                  {labelKey ? t(labelKey) : r.market}
                </HexTag>
                {sub && (
                  <Typography.Text
                    type={lapsed ? undefined : "secondary"}
                    style={{
                      fontSize: fontSize.caption,
                      whiteSpace: "nowrap",
                      ...(lapsed && subMeta ? { color: subMeta.color } : {}),
                    }}
                  >
                    {lapsed && subMeta
                      ? t(subMeta.labelKey)
                      : t("tenants.expiresAt", { date: formatDate(sub.expires_at) })}
                  </Typography.Text>
                )}
              </Space>
            );
          },
        },
        {
          title: t("tenants.colSpec"),
          render: (_, r) => `${String(r.spec.gpu_model)} × ${r.gpu_count}`,
        },
        {
          title: t("tenants.colNode"),
          dataIndex: "node_name",
          render: (v: string | null) => v ?? "—",
        },
        { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
      ]}
    />
    </>
  );
}

/** 配额覆盖:三个数字可留空(=该维走默认链),全空保存 = 清除覆盖;note 必填。 */
function QuotaTab({ userId }: { userId: number }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { message } = App.useApp();
  const errText = useApiErrorText();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const quota = useTenantQuota(userId);
  const setQuota = useSetTenantQuota();
  const qc = useQueryClient();
  const [form] = Form.useForm<{
    max_gpus: number | null;
    max_instances: number | null;
    max_disks: number | null;
    note: string;
  }>();

  if (quota.isLoading) return <Skeleton active paragraph={{ rows: 3 }} />;
  // 查询失败绝不渲染成「没有配额数据」(静默 null 会被误读成无覆盖)
  if (quota.isError) return <DataErrorAlert onRetry={() => void quota.refetch()} />;
  const q = quota.data;
  if (!q) return null;

  const fields: Array<{ name: "max_gpus" | "max_instances" | "max_disks"; label: string }> = [
    { name: "max_gpus", label: t("tenants.quota.maxGpus") },
    { name: "max_instances", label: t("tenants.quota.maxInstances") },
    { name: "max_disks", label: t("tenants.quota.maxDisks") },
  ];

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Text type="secondary">{t("tenants.quota.hint")}</Typography.Text>
      <Form
        form={form}
        layout="inline"
        initialValues={{
          max_gpus: q.max_gpus,
          max_instances: q.max_instances,
          max_disks: q.max_disks,
          note: "",
        }}
        onFinish={async (v) => {
          try {
            await setQuota.mutateAsync({
              userId,
              data: {
                max_gpus: v.max_gpus ?? null,
                max_instances: v.max_instances ?? null,
                max_disks: v.max_disks ?? null,
                note: v.note.trim(),
              },
            });
            message.success(t("tenants.quota.saved"));
            form.resetFields(["note"]);
            await qc.invalidateQueries({ queryKey: quota.queryKey });
          } catch (e) {
            message.error(errText(e, t("tenants.quota.saveFailed")));
          }
        }}
      >
        <Space wrap size={12}>
          {fields.map((f) => (
            <Form.Item key={f.name} name={f.name} label={f.label} style={{ marginBottom: 8 }}>
              <InputNumber min={1} precision={0} style={{ width: 110 }} disabled={!writable} />
            </Form.Item>
          ))}
          <Form.Item
            name="note"
            label={t("tenants.quota.note")}
            style={{ marginBottom: 8 }}
            rules={[
              {
                validator: (_, v: string | undefined) =>
                  v && v.trim().length >= 2
                    ? Promise.resolve()
                    : Promise.reject(new Error(t("tenants.quota.noteRequired"))),
              },
            ]}
          >
            <Input style={{ width: 220 }} disabled={!writable} />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" loading={setQuota.isPending} disabled={!writable}>
              {t("tenants.quota.save")}
            </Button>
          </Form.Item>
        </Space>
      </Form>
      <Space size={24} wrap>
        <span>
          {t("tenants.quota.effective")}:
          <b>
            {t("tenants.quota.effectiveLine", {
              gpus: q.effective_max_gpus,
              instances: q.effective_max_instances,
              disks: q.effective_max_disks,
            })}
          </b>
        </span>
        {q.updated_by != null && q.updated_at && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("tenants.quota.updatedBy", {
              id: q.updated_by,
              time: formatDateTime(q.updated_at),
            })}
            {q.note ? ` · ${q.note}` : ""}
          </Typography.Text>
        )}
      </Space>
    </Space>
  );
}

/** 事件时间线:实例选择器 + 该实例的状态迁移事件(倒序,游标加载更多)。 */
function EventsTab({ instances }: { instances: AdminInstanceOut[] }) {
  const { t } = useTranslation(["admin", "shared"]);
  const [uuid, setUuid] = useState<string | null>(null);
  const events = useInstanceEvents(uuid);
  const rows: InstanceEvent[] = events.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder={t("tenants.events.instancePlaceholder")}
        style={{ width: 320, marginBottom: 12 }}
        value={uuid}
        onChange={(v: string | undefined) => setUuid(v ?? null)}
        options={instances.map((i) => ({
          value: i.uuid,
          label: `${i.name} (${i.uuid.slice(0, 8)})`,
        }))}
      />
      <Table<InstanceEvent>
        size="small"
        rowKey="id"
        loading={uuid !== null && events.isLoading}
        pagination={false}
        scroll={{ y: 420 }}
        dataSource={rows}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={uuid !== null && events.isError}
              isForbidden={uuid !== null && isApiError(events.error) && events.error.status === 403}
              onRetry={() => void events.refetch()}
            >
              {uuid ? t("tenants.events.empty") : t("tenants.events.pickFirst")}
            </TableErrorEmpty>
          ),
        }}
        columns={[
          { title: t("tenants.colTime"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.events.colTransition"),
            render: (_, r) => {
              const from = r.from_status ? metaOf(instanceStatusMap, r.from_status) : undefined;
              const to = metaOf(instanceStatusMap, r.to_status);
              return (
                <Space size={4}>
                  <span>{r.from_status ? (from ? t(from.labelKey) : r.from_status) : "—"}</span>
                  <span>→</span>
                  <HexTag color={to?.color}>{to ? t(to.labelKey) : r.to_status}</HexTag>
                </Space>
              );
            },
          },
          { title: t("tenants.events.colReason"), dataIndex: "reason" },
          { title: t("tenants.events.colActor"), dataIndex: "actor", width: 90 },
        ]}
      />
      <LoadMore
        hasNextPage={Boolean(events.hasNextPage)}
        loading={events.isFetchingNextPage}
        isError={events.isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void events.fetchNextPage()}
      />
    </>
  );
}
