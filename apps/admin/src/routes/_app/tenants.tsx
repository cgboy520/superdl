import { adminColors, formatDateTime, instanceStatusMap, metaOf, skuTierMap, type InstanceStatus } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { App, Badge, Button, Card, Select, Space, Table, Tabs, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type InstanceOut,
  type TenantRow,
  useAdminInstances,
  useForceStop,
  useFreezeTenant,
  useTenants,
  useUnfreezeTenant,
} from "../../api";
import { useFormat } from "../../lib/format";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/tenants")({
  component: TenantsPage,
});

function TenantsTab() {
  const { t: tt } = useTranslation();
  const { formatMoney } = useFormat();
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
        { title: tt("tenants.colPhone"), dataIndex: "phone_masked" },
        {
          title: tt("tenants.colBalance"),
          dataIndex: "balance",
          render: (v: string) => (
            <span style={{ color: Number(v) <= 0 ? adminColors.negative : undefined }}>{formatMoney(v)}</span>
          ),
        },
        { title: tt("tenants.colTotalConsumed"), dataIndex: "total_consumed", render: (v: string) => formatMoney(v) },
        { title: tt("tenants.colInstances"), dataIndex: "instances", width: 70 },
        { title: tt("tenants.colDisk"), dataIndex: "disk_gb", render: (v: number) => `${v} GB`, width: 90 },
        {
          title: tt("tenants.colStatus"),
          dataIndex: "status",
          render: (v: string) =>
            v === "active" ? <Tag color="green">{tt("tenants.active")}</Tag> : <Tag color="red">{tt("tenants.frozen")}</Tag>,
        },
        { title: tt("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
        {
          title: tt("tenants.colActions"),
          render: (_, t) =>
            t.status === "active" ? (
              <ReasonAction
                label={tt("tenants.freeze")}
                danger
                title={tt("tenants.freezeTitle")}
                confirmText={tt("tenants.freezeConfirm", { id: t.id, phone: t.phone_masked })}
                disabled={!writable}
                disabledReason={tt("tenants.noPermission")}
                onSubmit={async (reason) => {
                  await freeze.mutateAsync({ userId: t.id, data: { reason } });
                  refresh();
                }}
              />
            ) : (
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
            ),
        },
      ]}
    />
  );
}

function InstancesTab() {
  const { t } = useTranslation(["admin", "shared"]);
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
          placeholder={t("tenants.statusFilter")}
          style={{ width: 160 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(instanceStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
      </Space>
      <Table<InstanceOut>
        scroll={{ x: 1000 }}
        rowKey="uuid"
        dataSource={instances ?? []}
        columns={[
          { title: t("tenants.colInstance"), dataIndex: "name" },
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
                  <Tag color={tm?.color}>{tm ? t(tm.labelKey) : String(r.spec.tier)}</Tag>
                </Space>
              );
            },
          },
          { title: t("tenants.colSshPort"), dataIndex: "ssh_port", width: 100 },
          { title: t("tenants.colCreatedAt"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("tenants.colActions"),
            render: (_, r) => {
              const isEco = r.spec.tier === "shared_eco";
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
                  <Tooltip title={isEco ? t("tenants.evictP1") : t("tenants.evictEcoOnly")}>
                    <Button size="small" disabled={!isEco}
                      onClick={() => message.info(t("tenants.evictP1"))}>
                      {t("tenants.evict")}
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
