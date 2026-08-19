import { formatHourlyPrice, skuTierMap, type SkuTier } from "@superdl/ui";
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
  message,
} from "antd";
import { useState } from "react";

import {
  type SkuAdminOut,
  isApiError,
  useAdminSkus,
  useCreateSku,
  useUpdateSku,
} from "../../api";
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
  const { modal } = App.useApp();
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
        message.success("SKU 已创建(默认下架)");
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "创建失败"),
    },
  });
  const update = useUpdateSku({
    mutation: {
      onSuccess: () => {
        message.success("已保存(变更仅影响新实例)");
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "保存失败"),
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
        title: "显存超卖超过 1.2,确认提交?",
        content: "显存超卖过高会显著增加共享池 OOM 互扰风险,请确认已有压测数据支撑。",
        okText: "确认提交",
        okButtonProps: { danger: true },
        onOk: doSubmit,
      });
    } else {
      doSubmit();
    }
  };

  return (
    <Card
      title="SKU 与定价"
      extra={
        <Tooltip title={writable ? "" : "只读角色不可创建"}>
          <Button type="primary" disabled={!writable} onClick={() => openEdit("new")}>
            新建 SKU
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
          { title: "名称", dataIndex: "name" },
          { title: "卡型", dataIndex: "gpu_model" },
          {
            title: "档位",
            dataIndex: "tier",
            render: (v: SkuTier) => (
              <Tag color={skuTierMap[v]?.color}>{skuTierMap[v]?.label ?? v}</Tag>
            ),
          },
          {
            title: "切分",
            render: (_, r) =>
              r.tier === "mig"
                ? r.mig_profile
                : `${r.gpu_cores_pct}% 算力 · ${r.vram_gb}G 显存`,
          },
          { title: "算力超卖", dataIndex: "oversell_cores", render: (v: string) => `${v}×` },
          {
            title: "显存超卖",
            dataIndex: "oversell_vram",
            render: (v: string) =>
              Number(v) > 1.2 ? <Tag color="orange">{v}×</Tag> : `${v}×`,
          },
          { title: "单价", dataIndex: "price_hourly", render: formatHourlyPrice },
          {
            title: "上架",
            dataIndex: "status",
            render: (v: string, r) => (
              <Tooltip title={writable ? "" : "只读角色不可操作"}>
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
            title: "操作",
            render: (_, r) => (
              <Tooltip title={writable ? "" : "只读角色不可编辑"}>
                <Button size="small" disabled={!writable} onClick={() => openEdit(r)}>
                  编辑
                </Button>
              </Tooltip>
            ),
          },
        ]}
      />
      <Drawer
        title={editing === "new" ? "新建 SKU" : `编辑 SKU · ${editing?.name ?? ""}`}
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={480}
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={submit}>
            提交
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {editing === "new" && (
            <>
              <Form.Item name="gpu_model" label="GPU 型号" rules={[{ required: true }]}>
                <Input placeholder="如 RTX4090 / A100 / H100" />
              </Form.Item>
              <Form.Item name="tier" label="档位" rules={[{ required: true }]}>
                <Select
                  options={Object.entries(skuTierMap).map(([v, m]) => ({
                    value: v,
                    label: m.label,
                  }))}
                />
              </Form.Item>
              <Form.Item name="mig_profile" label="MIG profile(仅 MIG 档)">
                <Input placeholder="如 1g.10gb" />
              </Form.Item>
            </>
          )}
          <Form.Item name="pool_label" label="节点池" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "kata", label: "kata(整卡直通)" },
                { value: "hami", label: "hami(共享软切分)" },
                { value: "mig", label: "mig(硬件切分)" },
              ]}
            />
          </Form.Item>
          <Form.Item name="gpu_cores_pct" label="算力份额 %" rules={[{ required: true }]}>
            <InputNumber min={1} max={100} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="vram_gb" label="显存配额 GB" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="超卖参数为高风险配置"
            description="变更仅影响新实例;显存超卖 >1.2 需二次确认。上调前须有同卡互扰压测数据(P95 利用率 <60%)。"
          />
          <Form.Item name="oversell_cores" label="算力超卖 ×" rules={[{ required: true }]}>
            <InputNumber min={1} max={9.99} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="oversell_vram" label="显存超卖 ×" rules={[{ required: true }]}>
            <InputNumber min={1} max={9.99} step={0.05} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="vcpu" label="vCPU" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="mem_gb" label="内存 GB" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="disk_gb" label="实例盘 GB" rules={[{ required: true }]}>
            <InputNumber min={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="price_hourly" label="单价(元/时)" rules={[{ required: true }]}>
            <InputNumber min={0.0001} step={0.01} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="max_gpus_per_instance" label="单实例最大 GPU 数">
            <InputNumber min={1} max={8} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="cuda_max" label="最高 CUDA 版本">
            <Input placeholder="如 12.8" />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
