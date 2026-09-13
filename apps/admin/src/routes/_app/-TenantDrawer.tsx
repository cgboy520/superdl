/** 租户下钻抽屉:实名摘要 + 账单/流水/订单/实例/在线服务/配额/事件 七 Tab + 跳审计。 */

import {
  controlWidth,
  drawerWidth,
  flattenPages,
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
import { CursorTable, DataErrorAlert, EmptyState, HexTag, Mono } from "@superdl/ui/components";
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

import { StatusTag } from "@superdl/ui/components";

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
import { useOrderColumns } from "../../components/orderColumns";
import { SignedAmount } from "../../components/SignedAmount";
import { useApiErrorText } from "@superdl/ui";
import { useCsvExport } from "@superdl/ui";
import { useFormat } from "@superdl/ui";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { AdminServicesTable } from "./-AdminServicesTable";

// 抽屉 Tab 白名单(tenants 路由 ?dtab= 校验共用)
export const DRAWER_TABS = ["bills", "ledger", "orders", "instances", "services", "quota", "events"] as const;
export type DrawerTab = (typeof DRAWER_TABS)[number];

export function TenantDrawer({
  tenant,
  dtab,
  onTabChange,
  onClose,
}: {
  tenant: TenantRow | null;
  /** 抽屉 Tab(受控,?dtab=) */
  dtab?: DrawerTab;
  onTabChange?: (tab: DrawerTab) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  // 抽屉级实例列表(前 100 条),三处复用
  const tenantInstances = useAdminInstances(tenant ? { user_id: tenant.id } : undefined, {
    enabled: tenant !== null,
    limit: 100,
  });
  const instances = flattenPages(tenantInstances.data);
  // 租户实例精确计数(判断是否截断)
  const instancesTotal = tenantInstances.data?.pages[0]?.total ?? null;

  return (
    <Drawer
      size={drawerWidth.lg}
      open={tenant !== null}
      onClose={onClose}
      title={tenant ? t("tenants.drawerTitle", { id: tenant.id, phone: tenant.phone_masked }) : undefined}
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

/** 小时账单:可按实例过滤;游标加载更多。 */
function BillsTab({ userId, instances }: { userId: number; instances: AdminInstanceOut[] }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney, formatHourlyPrice, formatDuration } = useFormat();
  const [instanceId, setInstanceId] = useState<number | null>(null);
  const bills = useTenantBills(userId, instanceId);
  const billRows = flattenPages(bills.data);

  return (
    <>
      <Select
        allowClear
        showSearch={{ optionFilterProp: "label" }}
        placeholder={t("tenants.billsInstanceFilter")}
        style={{ width: controlWidth.md, marginBottom: 12 }}
        value={instanceId}
        onChange={(v: number | undefined) => setInstanceId(v ?? null)}
        options={instances.map((i) => ({
          value: i.id,
          label: `${i.name} (${i.uuid.slice(0, 8)})`,
        }))}
      />
      <CursorTable
        query={bills}
        rows={billRows}
        compact
        emptyNode={
          <EmptyState
            scene={instanceId != null ? "search" : "list"}
            compact
            secondaryAction={
              instanceId != null ? (
                <Button size="small" onClick={() => setInstanceId(null)}>
                  {t("filter.clear", { ns: "shared" })}
                </Button>
              ) : undefined
            }
          />
        }
        size="small"
        rowKey="id"
        scroll={{ y: 420 }}
        columns={[
          { title: t("tenants.colHour"), dataIndex: "hour_start", render: formatDateTime },
          { title: t("tenants.colInstanceId"), dataIndex: "instance_id", width: 90 },
          {
            title: t("tenants.colSeconds"),
            dataIndex: "seconds_used",
            align: "right",
            render: (v: number) => formatDuration(v),
          },
          {
            title: t("tenants.colUnitPrice"),
            dataIndex: "unit_price",
            align: "right",
            render: (v: string) => formatHourlyPrice(v),
          },
          { title: t("tenants.colAmount"), dataIndex: "amount", align: "right", render: (v: string) => formatMoney(v) },
        ]}
      />
    </>
  );
}

function LedgerTab({ userId }: { userId: number }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const ledger = useTenantLedger(userId);
  const ledgerRows = flattenPages(ledger.data);
  const { doExport, exporting } = useCsvExport((tz, lang) => exportTenantLedgerCsv(userId, tz, lang));

  return (
    <>
      <div style={{ marginBottom: 8, textAlign: "right" }}>
        <Button size="small" onClick={() => void doExport()} loading={exporting}>
          {t("common.exportCsv")}
        </Button>
      </div>
      <CursorTable
        query={ledger}
        rows={ledgerRows}
        compact
        emptyNode={<EmptyState scene="list" compact />}
        size="small"
        rowKey="id"
        scroll={{ y: 420 }}
        columns={[
          { title: t("tenants.colTime"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.colType"),
            dataIndex: "type",
            width: 90,
            render: (v: string) => {
              return <StatusTag map={ledgerTypeMap} value={v} />;
            },
          },
          {
            title: t("tenants.colAmount"),
            dataIndex: "amount",
            align: "right",
            render: (v: string) => <SignedAmount value={v} highlightNegative={false} />,
          },
          {
            title: t("tenants.colBalanceAfter"),
            dataIndex: "balance_after",
            align: "right",
            render: (v: string) => formatMoney(v),
          },
          { title: t("tenants.colRemark"), dataIndex: "remark", ellipsis: true },
        ]}
      />
    </>
  );
}

/** 该租户的充值订单(游标分页)。 */
function OrdersTab({ userId }: { userId: number }) {
  const orders = useOrders({ user_id: userId });
  const rows = flattenPages(orders.data);
  const columns = useOrderColumns({ withTenant: false });

  return (
    <>
      <CursorTable<OrderRow>
        query={orders}
        rows={rows}
        compact
        emptyNode={<EmptyState scene="list" compact />}
        size="small"
        rowKey="order_no"
        scroll={{ y: 420 }}
        columns={columns}
      />
    </>
  );
}

/** 实例只读视图(前 100 条;写操作在「全局实例」Tab)。 */
function TenantInstancesTab({ instances, total }: { instances: AdminInstanceOut[]; total: number | null }) {
  const { t } = useTranslation(["admin", "shared"]);
  return (
    <>
      {/* 超过 100 台明示截断 */}
      {total !== null && total > instances.length && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 8, fontSize: fontSize.caption }}>
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
                  <Mono truncate={8}>{r.uuid}</Mono>
                </Typography.Text>
              </Space>
            ),
          },
          {
            title: t("tenants.colStatus"),
            dataIndex: "status",
            width: 110,
            render: (v: string) => {
              return <StatusTag map={instanceStatusMap} value={v} />;
            },
          },
          {
            // 购买模式标签取 packages/ui 映射;到期信息取内联 subscription
            title: t("tenants.colMarket"),
            width: 150,
            render: (_, r) => {
              const labelKey = marketLabelKey(r.market, r.subscription?.period);
              const sub = r.subscription;
              const subMeta = sub ? metaOf(subscriptionStatusMap, sub.status) : undefined;
              const lapsed = sub != null && sub.status !== "active";
              return (
                <Space orientation="vertical" size={0}>
                  <HexTag color={metaOf(marketMap, r.market)?.color}>{labelKey ? t(labelKey) : r.market}</HexTag>
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
            render: (v: string | null) => (v ? <Mono>{v}</Mono> : "—"),
          },
          { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
    </>
  );
}

/** 配额覆盖:留空 = 走默认链,全空保存 = 清除覆盖;note 必填。 */
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
  if (quota.isError) return <DataErrorAlert onRetry={() => void quota.refetch()} />;
  const q = quota.data;
  if (!q) return null;

  const fields: { name: "max_gpus" | "max_instances" | "max_disks"; label: string }[] = [
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
        onFinish={(v) => {
          void (async () => {
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
          })();
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

/** 事件时间线:实例选择器 + 状态迁移事件(倒序,游标)。 */
function EventsTab({ instances }: { instances: AdminInstanceOut[] }) {
  const { t } = useTranslation(["admin", "shared"]);
  const [uuid, setUuid] = useState<string | null>(null);
  const events = useInstanceEvents(uuid);
  const rows = flattenPages(events.data);

  return (
    <>
      <Select
        allowClear
        showSearch={{ optionFilterProp: "label" }}
        placeholder={t("tenants.events.instancePlaceholder")}
        style={{ width: controlWidth.lg, marginBottom: 12 }}
        value={uuid}
        onChange={(v: string | undefined) => setUuid(v ?? null)}
        options={instances.map((i) => ({
          value: i.uuid,
          label: `${i.name} (${i.uuid.slice(0, 8)})`,
        }))}
      />
      <CursorTable<InstanceEvent>
        query={events}
        rows={rows}
        compact
        emptyNode={
          <EmptyState
            scene="list"
            compact
            description={uuid ? t("tenants.events.empty") : t("tenants.events.pickFirst")}
          />
        }
        size="small"
        rowKey="id"
        scroll={{ y: 420 }}
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
    </>
  );
}
