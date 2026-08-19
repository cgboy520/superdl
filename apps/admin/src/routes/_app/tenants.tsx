import {
  formatDateTime,
  formatMoney,
  instanceStatusMap,
  skuTierMap,
  type InstanceStatus,
  type SkuTier,
} from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { App, Badge, Button, Card, Select, Space, Table, Tabs, Tag, Tooltip } from "antd";
import { useState } from "react";

import {
  type InstanceOut,
  type TenantRow,
  useAdminInstances,
  useForceStop,
  useFreezeTenant,
  useTenants,
  useUnfreezeTenant,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/tenants")({
  component: TenantsPage,
});

function TenantsTab() {
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, queryKey } = useTenants();
  const tenants: TenantRow[] = data ?? [];
  const freeze = useFreezeTenant();
  const unfreeze = useUnfreezeTenant();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <Table<TenantRow>
      scroll={{ x: 1000 }}
      rowKey="id"
      dataSource={tenants}
      columns={[
        { title: "ID", dataIndex: "id", width: 70 },
        { title: "手机", dataIndex: "phone_masked" },
        {
          title: "余额",
          dataIndex: "balance",
          render: (v: string) => (
            <span style={{ color: Number(v) <= 0 ? "#F87171" : undefined }}>{formatMoney(v)}</span>
          ),
        },
        { title: "累计消费", dataIndex: "total_consumed", render: (v: string) => formatMoney(v) },
        { title: "实例", dataIndex: "instances", width: 70 },
        { title: "数据盘", dataIndex: "disk_gb", render: (v: number) => `${v} GB`, width: 90 },
        {
          title: "状态",
          dataIndex: "status",
          render: (v: string) =>
            v === "active" ? <Tag color="green">正常</Tag> : <Tag color="red">已冻结</Tag>,
        },
        { title: "注册时间", dataIndex: "created_at", render: formatDateTime },
        {
          title: "操作",
          render: (_, t) =>
            t.status === "active" ? (
              <ReasonAction
                label="冻结"
                danger
                title="冻结租户"
                confirmText={`确认冻结租户 ${t.id}(${t.phone_masked})?冻结后其所有请求将被拒绝。`}
                disabled={!writable}
                disabledReason="当前角色无权操作"
                onSubmit={async (reason) => {
                  await freeze.mutateAsync({ userId: t.id, data: { reason } });
                  refresh();
                }}
              />
            ) : (
              <ReasonAction
                label="解冻"
                title="解冻租户"
                confirmText={`确认解冻租户 ${t.id}?`}
                disabled={!writable}
                disabledReason="当前角色无权操作"
                onSubmit={async (reason) => {
                  await unfreeze.mutateAsync({ userId: t.id, data: { reason } });
                  refresh();
                }}
              />
            ),
        },
      ]}
    />
  );
}

function InstancesTab() {
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [status, setStatus] = useState<string | undefined>();
  const qc = useQueryClient();
  const { data: instances, queryKey } = useAdminInstances(status ? { status } : undefined);
  const forceStop = useForceStop();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="状态过滤"
          style={{ width: 160 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(instanceStatusMap).map(([v, m]) => ({
            value: v,
            label: m.label,
          }))}
        />
      </Space>
      <Table<InstanceOut>
        scroll={{ x: 1000 }}
        rowKey="uuid"
        dataSource={instances ?? []}
        columns={[
          { title: "实例", dataIndex: "name" },
          {
            title: "状态",
            dataIndex: "status",
            render: (v: InstanceStatus) => (
              <Badge
                color={instanceStatusMap[v]?.color}
                text={instanceStatusMap[v]?.label ?? v}
              />
            ),
          },
          {
            title: "规格",
            render: (_, r) => {
              const tier = r.spec.tier as SkuTier;
              return (
                <Space>
                  <span>
                    {String(r.spec.gpu_model)} × {r.gpu_count}
                  </span>
                  <Tag color={skuTierMap[tier]?.color}>{skuTierMap[tier]?.label ?? tier}</Tag>
                </Space>
              );
            },
          },
          { title: "SSH 端口", dataIndex: "ssh_port", width: 100 },
          { title: "创建时间", dataIndex: "created_at", render: formatDateTime },
          {
            title: "操作",
            render: (_, r) => {
              const isEco = r.spec.tier === "shared_eco";
              return (
                <Space>
                  <ReasonAction
                    label="强制停止"
                    danger
                    title="强制停止实例"
                    confirmText={`确认强制停止实例 ${r.name}(${r.uuid.slice(0, 8)})?将立即结算尾账并通知用户。`}
                    disabled={!writable || r.status !== "running"}
                    disabledReason={!writable ? "当前角色无权操作" : "仅运行中的实例可强制停止"}
                    onSubmit={async (reason) => {
                      await forceStop.mutateAsync({ uuid: r.uuid, data: { reason } });
                      refresh();
                    }}
                  />
                  <Tooltip title={isEco ? "P1 功能,MVP 未开放" : "仅限经济档实例(SLA 已明示可重调度)"}>
                    <Button size="small" disabled={!isEco}
                      onClick={() => message.info("驱逐重调度为 P1 功能,当前版本未开放")}>
                      驱逐重调度
                    </Button>
                  </Tooltip>
                </Space>
              );
            },
          },
        ]}
      />
    </>
  );
}

function TenantsPage() {
  return (
    <Card title="租户与实例">
      <Tabs
        items={[
          { key: "tenants", label: "租户", children: <TenantsTab /> },
          { key: "instances", label: "全局实例", children: <InstancesTab /> },
        ]}
      />
    </Card>
  );
}
