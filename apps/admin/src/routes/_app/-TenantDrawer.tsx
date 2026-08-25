/** 租户下钻抽屉:实名摘要 + 账单/流水/订单/实例/配额/事件 六 Tab + 跳审计。
 *
 * 从 tenants.tsx 拆出(dash 前缀 = 非路由组件,与 -AdminsTab 同款约定):
 * 抽屉数据全部按 user_id / uuid 反查,实例选择器在账单过滤与事件时间线间复用同一份列表。
 */

import {
  adminColors,
  formatDateTime,
  instanceStatusMap,
  ledgerTypeMap,
  metaOf,
  orderStatusMap,
  paymentChannelMap,
} from "@superdl/ui";
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
  Space,
  Spin,
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
  useAdminInstances,
  useInstanceEvents,
  useOrders,
  useSetTenantQuota,
  useTenantBills,
  useTenantLedger,
  useTenantQuota,
} from "../../api";
import { LoadMoreButton } from "../../components/LoadMore";
import { StatusTag } from "../../components/StatusTag";
import { useApiErrorText } from "../../lib/apiError";
import { useCsvExport } from "../../lib/csvExport";
import { useFormat } from "../../lib/format";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export function TenantDrawer({
  tenant,
  onClose,
}: {
  tenant: TenantRow | null;
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
            {tenant.company_name && (
              <span>
                {t("tenants.colCompany")}:<b>{tenant.company_name}</b>
              </span>
            )}
          </Space>
          <Tabs
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
                children: <TenantInstancesTab instances={instances} />,
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
      <LoadMoreButton
        visible={Boolean(bills.hasNextPage)}
        loading={bills.isFetchingNextPage}
        onClick={() => void bills.fetchNextPage()}
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
          { title: t("tenants.colRemark"), dataIndex: "remark", ellipsis: true },
        ]}
      />
      <LoadMoreButton
        visible={Boolean(ledger.hasNextPage)}
        loading={ledger.isFetchingNextPage}
        onClick={() => void ledger.fetchNextPage()}
      />
    </>
  );
}

/** 订单反查:该租户的充值订单(游标分页,加载更多)。 */
function OrdersTab({ userId }: { userId: number }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const orders = useOrders({ user_id: userId });
  const rows: OrderRow[] = orders.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <Table<OrderRow>
        size="small"
        rowKey="order_no"
        loading={orders.isLoading}
        pagination={false}
        scroll={{ y: 420 }}
        dataSource={rows}
        columns={[
          { title: t("finance.colOrderNo"), dataIndex: "order_no" },
          {
            title: t("tenants.colAmount"),
            dataIndex: "amount",
            render: (v: string) => formatMoney(v),
          },
          {
            title: t("finance.colChannel"),
            dataIndex: "channel",
            width: 100,
            render: (v: string) => {
              const m = metaOf(paymentChannelMap, v);
              return m ? t(m.labelKey) : v;
            },
          },
          {
            title: t("tenants.colStatus"),
            dataIndex: "status",
            width: 100,
            render: (v: string) => {
              const m = metaOf(orderStatusMap, v);
              return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
            },
          },
          { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      <LoadMoreButton
        visible={Boolean(orders.hasNextPage)}
        loading={orders.isFetchingNextPage}
        onClick={() => void orders.fetchNextPage()}
      />
    </>
  );
}

/** 实例反查:只读视图(写操作集中在「全局实例」Tab,口径单一;抽屉取前 100 条)。 */
function TenantInstancesTab({ instances }: { instances: AdminInstanceOut[] }) {
  const { t } = useTranslation(["admin", "shared"]);
  return (
    <Table<AdminInstanceOut>
      size="small"
      rowKey="uuid"
      pagination={false}
      scroll={{ y: 420 }}
      dataSource={instances}
      columns={[
        {
          title: t("tenants.colInstance"),
          render: (_, r) => (
            <Space size={8}>
              <span>{r.name}</span>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
            return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
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

  if (quota.isLoading) return <Spin />;
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
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
        locale={{ emptyText: uuid ? t("tenants.events.empty") : t("tenants.events.pickFirst") }}
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
                  <StatusTag color={to?.color}>{to ? t(to.labelKey) : r.to_status}</StatusTag>
                </Space>
              );
            },
          },
          { title: t("tenants.events.colReason"), dataIndex: "reason" },
          { title: t("tenants.events.colActor"), dataIndex: "actor", width: 90 },
        ]}
      />
      <LoadMoreButton
        visible={Boolean(events.hasNextPage)}
        loading={events.isFetchingNextPage}
        onClick={() => void events.fetchNextPage()}
      />
    </>
  );
}
