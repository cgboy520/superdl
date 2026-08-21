import { metaOf, skuTierMap, type SkuTier } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  App,
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Table,
  Tag,
  Tooltip,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type SkuAdminOut,
  useAdminSkus,
  useCreateSku,
  useUpdateSku,
} from "../../api";
import { useFormat } from "../../lib/format";
import { useApiErrorText } from "../../lib/apiError";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/skus")({
  component: SkusPage,
});

interface SkuFormValues {
  name: string;
  gpu_model: string;
  tier: string;
  mig_profile?: string | null;
  gpu_cores_pct: number;
  vram_gb: number;
  oversell_cores: number;
  oversell_vram: number;
  pool_label: string;
  vcpu: number;
  mem_gb: number;
  disk_gb: number;
  price_hourly: number;
  max_gpus_per_instance: number;
  cuda_max?: string | null;
}

function SkusPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatHourlyPrice } = useFormat();
  // 深色主题下必须走 useApp 实例:静态 message 拿不到 ConfigProvider token
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data: skus, refetch, queryKey } = useAdminSkus();
  const [editing, setEditing] = useState<SkuAdminOut | "new" | null>(null);
  const [form] = Form.useForm<SkuFormValues>();

  const refresh = () => {
    void qc.invalidateQueries({ queryKey });
    void refetch();
  };
  const create = useCreateSku({
    mutation: {
      onSuccess: () => {
        message.success(t("skus.created"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("skus.createFailed"))),
    },
  });
  const update = useUpdateSku({
    mutation: {
      onSuccess: () => {
        message.success(t("skus.saved"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("skus.saveFailed"))),
    },
  });

  const openEdit = (sku: SkuAdminOut | "new") => {
    setEditing(sku);
    if (sku === "new") {
      form.resetFields();
      form.setFieldsValue({
        tier: "shared_std", gpu_cores_pct: 50, oversell_cores: 1.5, oversell_vram: 1.0,
        disk_gb: 100, max_gpus_per_instance: 1, pool_label: "hami", vcpu: 8, mem_gb: 32,
      });
    } else {
      form.setFieldsValue({
        ...sku,
        oversell_cores: Number(sku.oversell_cores),
        oversell_vram: Number(sku.oversell_vram),
        price_hourly: Number(sku.price_hourly),
      });
    }
  };

  const submit = async () => {
    const values = await form.validateFields();
    const doSubmit = () => {
      const payload = {
        ...values,
        oversell_cores: String(values.oversell_cores),
        oversell_vram: String(values.oversell_vram),
        price_hourly: String(values.price_hourly),
      };
      if (editing === "new") {
        create.mutate({ data: payload as never });
      } else if (editing) {
        update.mutate({ skuId: editing.id, data: payload as never });
      }
    };
    if (values.oversell_vram > 1.2) {
      modal.confirm({
        title: t("skus.vramOversellConfirmTitle"),
        content: t("skus.vramOversellConfirmBody"),
        okText: t("skus.confirmSubmit"),
        okButtonProps: { danger: true },
        onOk: doSubmit,
      });
    } else {
      doSubmit();
    }
  };

  return (
    <Card
      title={t("menu.skus")}
      extra={
        <Tooltip title={writable ? "" : t("skus.readonlyNoCreate")}>
          <Button type="primary" disabled={!writable} onClick={() => openEdit("new")}>
            {t("skus.newSku")}
          </Button>
        </Tooltip>
      }
    >
      <Table<SkuAdminOut>
        scroll={{ x: 1000 }}
        rowKey="id"
        dataSource={skus ?? []}
        pagination={false}
        columns={[
          { title: t("skus.colName"), dataIndex: "name" },
          { title: t("skus.colGpuModel"), dataIndex: "gpu_model" },
          {
            title: t("skus.colTier"),
            dataIndex: "tier",
            render: (v: SkuTier) => {
              const m = metaOf(skuTierMap, v);
              return <Tag color={m?.color}>{m ? t(m.labelKey) : v}</Tag>;
            },
          },
          {
            title: t("skus.colSlice"),
            render: (_, r) =>
              r.tier === "mig"
                ? r.mig_profile
                : t("skus.sliceShared", { pct: r.gpu_cores_pct, vram: r.vram_gb }),
          },
          { title: t("skus.colOversellCores"), dataIndex: "oversell_cores", render: (v: string) => `${v}×` },
          {
            title: t("skus.colOversellVram"),
            dataIndex: "oversell_vram",
            render: (v: string) =>
              Number(v) > 1.2 ? <Tag color="orange">{v}×</Tag> : `${v}×`,
          },
          { title: t("skus.colPrice"), dataIndex: "price_hourly", render: (v: string) => formatHourlyPrice(v) },
          {
            title: t("skus.colOnSale"),
            dataIndex: "status",
            render: (v: string, r) => (
              <Tooltip title={writable ? "" : t("nodes.readonlyNoOp")}>
                <Switch
                  checked={v === "on"}
                  disabled={!writable}
                  onChange={(on) =>
                    update.mutate({ skuId: r.id, data: { status: on ? "on" : "off" } })
                  }
                />
              </Tooltip>
            ),
          },
          {
            title: t("skus.colActions"),
            render: (_, r) => (
              <Tooltip title={writable ? "" : t("skus.readonlyNoEdit")}>
                <Button size="small" disabled={!writable} onClick={() => openEdit(r)}>
                  {t("skus.edit")}
                </Button>
              </Tooltip>
            ),
          },
        ]}
      />
      <Drawer
        title={editing === "new" ? t("skus.newSku") : t("skus.editTitle", { name: editing?.name ?? "" })}
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={480}
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={submit}>
            {t("skus.submit")}
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t("skus.colName")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {editing === "new" && (
            <>
              <Form.Item name="gpu_model" label={t("skus.gpuModelLabel")} rules={[{ required: true }]}>
                <Input placeholder={t("skus.gpuModelPlaceholder")} />
              </Form.Item>
              <Form.Item name="tier" label={t("skus.colTier")} rules={[{ required: true }]}>
                <Select
                  options={Object.entries(skuTierMap).map(([v, m]) => ({
                    value: v,
                    label: t(m.labelKey),
                  }))}
                />
              </Form.Item>
              <Form.Item name="mig_profile" label={t("skus.migProfileLabel")}>
                <Input placeholder={t("skus.migProfilePlaceholder")} />
              </Form.Item>
            </>
          )}
          <Form.Item name="pool_label" label={t("nodes.poolLabel")} rules={[{ required: true }]}>
            <Select
              options={[
                { value: "kata", label: t("nodes.poolKata") },
                { value: "hami", label: t("nodes.poolHami") },
                { value: "mig", label: t("nodes.poolMig") },
              ]}
            />
          </Form.Item>
          <Form.Item name="gpu_cores_pct" label={t("skus.coresPctLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} max={100} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="vram_gb" label={t("skus.vramLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message={t("skus.oversellRisk")}
            description={t("skus.oversellRiskDesc")}
          />
          <Form.Item name="oversell_cores" label={t("skus.oversellCoresLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} max={9.99} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="oversell_vram" label={t("skus.oversellVramLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} max={9.99} step={0.05} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="vcpu" label="vCPU" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="mem_gb" label={t("skus.memLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="disk_gb" label={t("skus.diskLabel")} rules={[{ required: true }]}>
            <InputNumber min={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="price_hourly" label={t("skus.priceLabel")} rules={[{ required: true }]}>
            <InputNumber min={0.0001} step={0.01} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="max_gpus_per_instance" label={t("skus.maxGpusLabel")}>
            <InputNumber min={1} max={8} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="cuda_max" label={t("skus.cudaMaxLabel")}>
            <Input placeholder={t("images.cudaPlaceholder")} />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
