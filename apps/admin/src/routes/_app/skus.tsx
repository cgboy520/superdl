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
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type CapacityWarning,
  type GpuModelAggregate,
  type SkuAdminOut,
  useAdminSkus,
  useCreateSku,
  useGpuModelAggregates,
  useSkuCapacityPreview,
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
  tier: SkuTier;
  mig_profile?: string | null;
  gpu_cores_pct: number;
  vram_gb: number;
  oversell_cores: number;
  oversell_vram: number;
  pool_label: string;
  vcpu: number;
  mem_gb: number;
  disk_gb: number;
  price_hourly: string;  // stringMode:单价 4 位小数,不经二进制浮点
  max_gpus_per_instance: number;
  cuda_max?: string | null;
}

const TIER_POOL: Record<SkuTier, string> = {
  dedicated: "kata",
  mig: "mig",
  shared_std: "hami",
  shared_eco: "hami",
};
const POOL_TIERS: Record<string, SkuTier[]> = {
  kata: ["dedicated"],
  mig: ["mig"],
  hami: ["shared_std", "shared_eco"],
};

type TFn = ReturnType<typeof useTranslation<["admin", "shared"]>>["t"];

function warnText(t: TFn, w: CapacityWarning): string {
  const p = w.params ?? {};
  switch (w.code) {
    case "unrecognized_model":
      return t("skus.warnUnrecognizedModel", { model: String(p.model ?? "") });
    case "no_ready_node":
      return t("skus.warnNoReadyNode", {
        model: String(p.model ?? ""),
        pool: String(p.pool ?? ""),
      });
    case "vram_exceeds_node":
      return t("skus.warnVramExceedsNode", {
        vram: Number(p.vram_gb ?? 0),
        nodeVram: Number(p.node_vram_gb ?? 0),
      });
  }
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
  const { data: aggregates } = useGpuModelAggregates();
  const [editing, setEditing] = useState<SkuAdminOut | "new" | null>(null);
  const [clusterPick, setClusterPick] = useState<GpuModelAggregate | null>(null);
  const [form] = Form.useForm<SkuFormValues>();

  const clusterOptions = useMemo(
    () => (aggregates ?? []).filter((a) => a.gpu_model && a.pool_label && POOL_TIERS[a.pool_label]),
    [aggregates],
  );

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
  // 上架开关独立 mutation:SKU_NOT_SELLABLE 走「强制上架」确认,不复用编辑弹窗的报错
  const toggleSale = useUpdateSku({
    mutation: {
      onSuccess: refresh,
      onError: (e, vars) => {
        const code = (e as { code?: string }).code;
        if (code === "SKU_NOT_SELLABLE" && !vars.force) {
          modal.confirm({
            title: t("skus.notSellableTitle"),
            content: errText(e, t("skus.toggleFailed")),
            okText: t("skus.forceOn"),
            okButtonProps: { danger: true },
            onOk: () => toggleSale.mutate({ ...vars, force: true }),
          });
          return;
        }
        message.error(errText(e, t("skus.toggleFailed")));
      },
    },
  });

  // 表单联动:实时容量预览参数(编辑态型号/档位不在表单里,取自记录)
  const record = editing !== null && editing !== "new" ? editing : null;
  const wModel = Form.useWatch("gpu_model", form);
  const wTier = Form.useWatch("tier", form);
  const wPool = Form.useWatch("pool_label", form);
  const wPct = Form.useWatch("gpu_cores_pct", form);
  const wOversell = Form.useWatch("oversell_cores", form);
  const wVram = Form.useWatch("vram_gb", form);
  const pModel = wModel ?? record?.gpu_model;
  const pTier = wTier ?? record?.tier;
  const pPool = wPool ?? record?.pool_label;
  const previewParams = useMemo(() => {
    if (editing === null || !pModel || !pTier || !pPool) return null;
    return {
      gpu_model: pModel,
      pool_label: pPool,
      tier: pTier,
      gpu_cores_pct: wPct ?? record?.gpu_cores_pct ?? 100,
      oversell_cores: String(wOversell ?? record?.oversell_cores ?? "1.00"),
      vram_gb: wVram ?? record?.vram_gb,
    };
  }, [editing, pModel, pTier, pPool, wPct, wOversell, wVram, record]);
  const preview = useSkuCapacityPreview(previewParams);

  const applyRecommend = (agg: GpuModelAggregate, tier: SkuTier, pct: number) => {
    const shared = tier.startsWith("shared");
    const factor = shared ? pct / 100 : 1;
    form.setFieldsValue({
      gpu_model: agg.gpu_model ?? "",
      pool_label: agg.pool_label ?? "",
      gpu_cores_pct: shared ? pct : 100,
      vram_gb: Math.max(1, Math.floor(agg.vram_gb * factor)),
      ...(agg.vcpu_per_gpu ? { vcpu: Math.max(1, Math.round(agg.vcpu_per_gpu * factor)) } : {}),
      ...(agg.mem_gb_per_gpu
        ? { mem_gb: Math.max(1, Math.round(agg.mem_gb_per_gpu * factor)) }
        : {}),
    });
  };

  const onClusterPick = (idx: number | "manual") => {
    if (idx === "manual") {
      setClusterPick(null);
      return;
    }
    const agg = clusterOptions[idx];
    if (!agg) return;
    setClusterPick(agg);
    const pool = agg.pool_label ?? "";
    const tiers = POOL_TIERS[pool] ?? [];
    const current = form.getFieldValue("tier") as SkuTier | undefined;
    const tier = current && tiers.includes(current) ? current : tiers[0];
    if (!tier) return;
    form.setFieldsValue({ tier });
    applyRecommend(agg, tier, form.getFieldValue("gpu_cores_pct") ?? 50);
  };

  const onTierChange = (tier: SkuTier) => {
    form.setFieldsValue({ pool_label: TIER_POOL[tier] });
    if (clusterPick) {
      applyRecommend(clusterPick, tier, form.getFieldValue("gpu_cores_pct") ?? 50);
    } else if (!tier.startsWith("shared")) {
      form.setFieldsValue({ gpu_cores_pct: 100 });
    }
  };

  const openEdit = (sku: SkuAdminOut | "new") => {
    setEditing(sku);
    setClusterPick(null);
    if (sku === "new") {
      form.resetFields();
      form.setFieldsValue({
        tier: "shared_std", gpu_cores_pct: 50, oversell_cores: 1.5, oversell_vram: 1.0,
        disk_gb: 100, max_gpus_per_instance: 1, pool_label: "hami", vcpu: 8, mem_gb: 32,
      });
    } else {
      form.setFieldsValue({
        ...sku,
        tier: sku.tier as SkuTier,
        oversell_cores: Number(sku.oversell_cores),
        oversell_vram: Number(sku.oversell_vram),
        price_hourly: sku.price_hourly,
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
        price_hourly: values.price_hourly,
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

  const isNew = editing === "new";
  const tierOptions = (
    isNew && clusterPick?.pool_label ? POOL_TIERS[clusterPick.pool_label] : Object.keys(skuTierMap)
  ) as SkuTier[];

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
        scroll={{ x: 1240 }}
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
          {
            title: t("skus.colCapacity"),
            dataIndex: "capacity_gpus",
            render: (v: number, r) =>
              v === 0 && r.status === "on" ? <Tag color="red">0</Tag> : v,
          },
          {
            title: t("skus.colSoldShare"),
            dataIndex: "sold_share",
            render: (v: string | null) => (v == null ? "—" : `${Math.round(Number(v) * 100)}%`),
          },
          {
            title: t("skus.colActualOversell"),
            render: (_, r) => {
              if (r.actual_oversell == null) return "—";
              const over = Number(r.actual_oversell) >= Number(r.oversell_cores);
              return over ? <Tag color="red">{r.actual_oversell}×</Tag> : `${r.actual_oversell}×`;
            },
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
                  loading={toggleSale.isPending}
                  onChange={(on) =>
                    toggleSale.mutate({ skuId: r.id, data: { status: on ? "on" : "off" } })
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
        title={isNew ? t("skus.newSku") : t("skus.editTitle", { name: record?.name ?? "" })}
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={760}
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={submit}>
            {t("skus.submit")}
          </Button>
        }
      >
        <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
          <Form form={form} layout="vertical" style={{ flex: 1, minWidth: 0 }}>
            {isNew && clusterOptions.length > 0 && (
              <Form.Item label={t("skus.fromClusterLabel")}>
                <Select<number | "manual">
                  placeholder={t("skus.fromClusterPlaceholder")}
                  onChange={onClusterPick}
                  options={[
                    ...clusterOptions.map((a, i) => ({
                      value: i,
                      label: `${a.gpu_model} · ${a.pool_label} · ${t("skus.modelOptionMeta", {
                        free: a.ready_gpu_free,
                        total: a.gpu_total,
                        vram: a.vram_gb,
                      })}`,
                    })),
                    { value: "manual" as const, label: t("skus.manualOption") },
                  ]}
                />
              </Form.Item>
            )}
            {isNew && clusterOptions.length === 0 && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message={t("skus.clusterEmptyHint")}
              />
            )}
            <Form.Item name="name" label={t("skus.colName")} rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            {isNew && (
              <>
                <Form.Item
                  name="gpu_model"
                  label={t("skus.gpuModelLabel")}
                  rules={[{ required: true }]}
                >
                  <Input
                    placeholder={t("skus.gpuModelPlaceholder")}
                    disabled={clusterPick !== null}
                  />
                </Form.Item>
                <Form.Item name="tier" label={t("skus.colTier")} rules={[{ required: true }]}>
                  <Select
                    onChange={onTierChange}
                    options={tierOptions.map((v) => ({
                      value: v,
                      label: t(skuTierMap[v].labelKey),
                    }))}
                  />
                </Form.Item>
                {wTier === "mig" && (
                  <Form.Item
                    name="mig_profile"
                    label={t("skus.migProfileLabel")}
                    rules={[{ required: true }]}
                  >
                    <Input
                      placeholder={t("skus.migProfilePlaceholder")}
                      onChange={(e) => {
                        const m = /(\d+)gb/i.exec(e.target.value);
                        if (m) form.setFieldsValue({ vram_gb: Number(m[1]) });
                      }}
                    />
                  </Form.Item>
                )}
              </>
            )}
            <Form.Item name="pool_label" label={t("nodes.poolLabel")} rules={[{ required: true }]}>
              <Select
                disabled={isNew}
                options={[
                  { value: "kata", label: t("nodes.poolKata") },
                  { value: "hami", label: t("nodes.poolHami") },
                  { value: "mig", label: t("nodes.poolMig") },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="gpu_cores_pct"
              label={t("skus.coresPctLabel")}
              rules={[{ required: true }]}
            >
              <InputNumber
                min={1}
                max={100}
                disabled={isNew && !!wTier && !wTier.startsWith("shared")}
                style={{ width: "100%" }}
                onChange={(v) => {
                  if (clusterPick && wTier && typeof v === "number") {
                    applyRecommend(clusterPick, wTier, v);
                  }
                }}
              />
            </Form.Item>
            <Form.Item name="vram_gb" label={t("skus.vramLabel")} rules={[{ required: true }]}>
              <InputNumber
                min={1}
                max={clusterPick?.vram_gb || undefined}
                style={{ width: "100%" }}
              />
            </Form.Item>
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message={t("skus.oversellRisk")}
              description={t("skus.oversellRiskDesc")}
            />
            <Form.Item
              name="oversell_cores"
              label={t("skus.oversellCoresLabel")}
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={9.99} step={0.1} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="oversell_vram"
              label={t("skus.oversellVramLabel")}
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={9.99} step={0.05} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="vcpu"
              label="vCPU"
              rules={[{ required: true }]}
              extra={
                clusterPick && clusterPick.vcpu_per_gpu > 0
                  ? t("skus.ratioHint", { model: clusterPick.gpu_model ?? "" })
                  : undefined
              }
            >
              <InputNumber min={1} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="mem_gb" label={t("skus.memLabel")} rules={[{ required: true }]}>
              <InputNumber min={1} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="disk_gb" label={t("skus.diskLabel")} rules={[{ required: true }]}>
              <InputNumber min={10} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="price_hourly" label={t("skus.priceLabel")} rules={[{ required: true }]}>
              <InputNumber min="0.0001" step="0.01" precision={4} stringMode style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="max_gpus_per_instance" label={t("skus.maxGpusLabel")}>
              <InputNumber min={1} max={8} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="cuda_max" label={t("skus.cudaMaxLabel")}>
              <Input placeholder={t("images.cudaPlaceholder")} />
            </Form.Item>
          </Form>
          <Card size="small" title={t("skus.previewTitle")} style={{ width: 248, flexShrink: 0 }}>
            {previewParams === null ? (
              <Typography.Text type="secondary">{t("skus.previewPending")}</Typography.Text>
            ) : preview.data ? (
              <>
                {[
                  [t("skus.previewNodes"), preview.data.matching_nodes],
                  [t("skus.previewReadyGpus"), preview.data.ready_gpus],
                  [t("skus.previewTotalGpus"), preview.data.total_gpus],
                  [t("skus.previewEst"), preview.data.est_instances],
                ].map(([label, value]) => (
                  <div
                    key={String(label)}
                    style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}
                  >
                    <Typography.Text type="secondary">{label}</Typography.Text>
                    <Typography.Text strong>{value}</Typography.Text>
                  </div>
                ))}
                {preview.data.warnings.map((w) => (
                  <Alert
                    key={w.code}
                    type="warning"
                    showIcon
                    style={{ marginTop: 8 }}
                    message={warnText(t, w)}
                  />
                ))}
              </>
            ) : (
              <Spin size="small" />
            )}
          </Card>
        </div>
      </Drawer>
    </Card>
  );
}
