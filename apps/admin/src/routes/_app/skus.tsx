import { adminColors, layout, metaOf, skuTierMap, skuVariant, type SkuTier, type SkuVariant } from "@superdl/ui";
import { HexTag, PageContainer, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
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
  type SkuCreate,
  type SkuUpdate,
  isApiError,
  useAdminSkus,
  useCreateSku,
  useGpuModelAggregates,
  useSkuCapacityPreview,
  useSkuImpact,
  useUpdateSku,
} from "../../api";
import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";
import { useFormDraft } from "@superdl/ui";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { REASON_MAX_LEN } from "../../lib/validators";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/skus")({
  component: SkusPage,
});

interface SkuFormValues {
  name: string;
  gpu_model: string;
  /** 表单只选展示档位,提交时派生 tier 与 pool_label;tier 不做表单字段。 */
  variant: SkuVariant;
  mig_profile?: string | null;
  gpu_cores_pct: number;
  vram_gb: number;
  oversell_cores: number;
  pool_label: string;
  vcpu: number;
  mem_gb: number;
  disk_gb: number;
  price_hourly: string;  // stringMode:单价 4 位小数,不经二进制浮点
  max_gpus_per_instance: number;
  cuda_max?: string | null;
  /** 是否接受包周期下单(与档位正交) */
  period_enabled: boolean;
  /** 是否上竞价档(与档位正交) */
  spot_enabled: boolean;
  /** 编辑必填(入审计);新建端点不接受 */
  reason?: string;
}

// 展示档位 →(落库档位, 节点池);与后端 catalog._check_tier_pool 同款约束
const VARIANT_SPEC: Record<SkuVariant, { tier: SkuTier; pool: string }> = {
  dedicated: { tier: "dedicated", pool: "kata" },
  shared_mig: { tier: "shared", pool: "mig" },
  shared_hami: { tier: "shared", pool: "hami" },
  // CPU 档默认 cpu 池,可改挂 hami(后端 TIER_POOLS 两者放行)
  cpu: { tier: "cpu", pool: "cpu" },
};
const POOL_VARIANTS: Record<string, SkuVariant[]> = {
  kata: ["dedicated"],
  mig: ["shared_mig"],
  hami: ["shared_hami", "cpu"],
  cpu: ["cpu"],
};
const ALL_VARIANTS = Object.keys(VARIANT_SPEC) as SkuVariant[];
/** CPU 规格提交时补零的 GPU 字段(镜像后端 catalog.cpu_spec_error);超卖钉成 1。 */
const CPU_ZERO_FIELDS = {
  gpu_model: "",
  mig_profile: null,
  gpu_cores_pct: 0,
  vram_gb: 0,
  max_gpus_per_instance: 0,
  oversell_cores: 1,
} as const;

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
  const { message } = App.useApp();
  const confirm = useConfirm();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data: skus, queryKey, isLoading, isError, error, refetch } = useAdminSkus();
  // 聚合端点只放 ops/readonly,按角色关停查询
  const { data: aggregates } = useGpuModelAggregates({
    enabled: canWriteOps(role) || role === "readonly",
  });
  const [editing, setEditing] = useState<SkuAdminOut | "new" | null>(null);
  const [clusterPick, setClusterPick] = useState<GpuModelAggregate | null>(null);
  const [form] = Form.useForm<SkuFormValues>();
  // 新建草稿(sessionStorage);编辑态不写草稿
  const draft = useFormDraft<SkuFormValues>("sku-new");

  const clusterOptions = useMemo(
    () => (aggregates ?? []).filter((a) => a.gpu_model && a.pool_label && POOL_VARIANTS[a.pool_label]),
    [aggregates],
  );

  const refresh = () => void qc.invalidateQueries({ queryKey });
  const create = useCreateSku({
    mutation: {
      onSuccess: () => {
        message.success(t("skus.created"));
        draft.clear();
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("common.createFailed"))),
    },
  });
  const update = useUpdateSku({
    mutation: {
      onSuccess: () => {
        message.success(t("skus.saved"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("common.saveFailed"))),
    },
  });
  // 错误提示统一由 ReasonAction 弹出
  const onSale = useUpdateSku({
    mutation: { onSuccess: refresh },
  });
  // 强制上架:modal.confirm 流程自带提示
  const forceOnSale = useUpdateSku({
    mutation: {
      onSuccess: refresh,
      onError: (e) => message.error(errText(e, t("skus.toggleFailed"))),
    },
  });

  // 下架:错误提示统一由 ReasonAction 弹出
  const offSale = useUpdateSku({
    mutation: { onSuccess: refresh },
  });

  // 容量预览参数(编辑态型号/档位取自记录)
  const record = editing !== null && editing !== "new" ? editing : null;
  // 改价影响面(编辑态才查)
  const impact = useSkuImpact(record?.id ?? null);
  const wModel = Form.useWatch("gpu_model", form);
  const wVariant = Form.useWatch("variant", form);
  const wPool = Form.useWatch("pool_label", form);
  const wPct = Form.useWatch("gpu_cores_pct", form);
  const wOversell = Form.useWatch("oversell_cores", form);
  const wVram = Form.useWatch("vram_gb", form);
  const wVcpu = Form.useWatch("vcpu", form);
  const wMem = Form.useWatch("mem_gb", form);
  const isCpuVariant = (wVariant ?? (record ? skuVariant(record.tier, record.pool_label) : undefined)) === "cpu";
  const pModel = wModel ?? record?.gpu_model;
  const pPool = wPool ?? record?.pool_label;
  // 折算口径只看池,端点不收 tier;CPU 规格 gpu_model 留空
  const previewParams = useMemo(() => {
    if (editing === null || !pPool) return null;
    if (isCpuVariant) {
      return {
        pool_label: pPool,
        vcpu: wVcpu ?? record?.vcpu,
        mem_gb: wMem ?? record?.mem_gb,
      };
    }
    if (!pModel) return null;
    return {
      gpu_model: pModel,
      pool_label: pPool,
      gpu_cores_pct: wPct ?? record?.gpu_cores_pct ?? 100,
      oversell_cores: String(wOversell ?? record?.oversell_cores ?? "1.00"),
      vram_gb: wVram ?? record?.vram_gb,
    };
  }, [editing, isCpuVariant, pModel, pPool, wPct, wOversell, wVram, wVcpu, wMem, record]);
  const preview = useSkuCapacityPreview(previewParams);

  const applyRecommend = (agg: GpuModelAggregate, variant: SkuVariant, pct: number) => {
    // 只有 HAMi 按算力份额折规格;整卡与 MIG 拿整份
    const shared = variant === "shared_hami";
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
    const variants = POOL_VARIANTS[pool] ?? [];
    const current = form.getFieldValue("variant") as SkuVariant | undefined;
    const variant = current && variants.includes(current) ? current : variants[0];
    if (!variant) return;
    onVariantChange(variant);
    // CPU 规格不套型号推荐
    if (variant !== "cpu") {
      applyRecommend(agg, variant, form.getFieldValue("gpu_cores_pct") ?? 50);
    } else {
      form.setFieldsValue({ pool_label: agg.pool_label ?? "hami" });
    }
  };

  const onVariantChange = (variant: SkuVariant) => {
    const { pool } = VARIANT_SPEC[variant];
    form.setFieldsValue({
      variant,
      // cpu 档默认 cpu 池,hami 需运营显式改
      pool_label: pool,
      // 切片只属于 mig 池,换走时清掉
      ...(variant === "shared_mig" ? {} : { mig_profile: null }),
    });
    if (variant === "cpu") {
      form.setFieldsValue(CPU_ZERO_FIELDS);
      return;
    }
    if (clusterPick) {
      applyRecommend(clusterPick, variant, form.getFieldValue("gpu_cores_pct") ?? 50);
    } else if (variant !== "shared_hami") {
      form.setFieldsValue({ gpu_cores_pct: 100 });
    }
  };

  const openEdit = (sku: SkuAdminOut | "new") => {
    setEditing(sku);
    setClusterPick(null);
    if (sku === "new") {
      form.resetFields();
      form.setFieldsValue({
        variant: "shared_hami", gpu_cores_pct: 50, oversell_cores: 1.5,
        disk_gb: 100, max_gpus_per_instance: 1, pool_label: "hami", vcpu: 8, mem_gb: 32,
        // 与后端 SkuCreate.period_enabled 默认值一致
        period_enabled: true,
        // 与后端 SkuCreate.spot_enabled 默认值一致
        spot_enabled: false,
        // 草稿覆盖默认值(仅新建)
        ...draft.load(),
      });
    } else {
      form.setFieldsValue({
        ...sku,
        variant: skuVariant(sku.tier, sku.pool_label),
        oversell_cores: Number(sku.oversell_cores),
        price_hourly: sku.price_hourly,
      });
    }
  };

  const submit = async () => {
    await form.validateFields();
    // 取值用 getFieldsValue(true):validateFields() 只回已挂载 Form.Item 的字段
    const values = form.getFieldsValue(true) as SkuFormValues;
    const { tier, pool: derivedPool } = VARIANT_SPEC[values.variant];
    // 只有 cpu 档的池可选,其余由档位派生
    const pool = values.variant === "cpu" ? values.pool_label : derivedPool;
    const gpuFields =
      tier === "cpu"
        ? CPU_ZERO_FIELDS
        : {
            gpu_model: values.gpu_model,
            mig_profile: values.mig_profile ?? null,
            gpu_cores_pct: values.gpu_cores_pct,
            vram_gb: values.vram_gb,
            max_gpus_per_instance: values.max_gpus_per_instance,
          };
    const doSubmit = () => {
      if (editing === "new") {
        // 新建端点不接受 reason
        const createPayload: SkuCreate = {
          name: values.name,
          tier,
          ...gpuFields,
          oversell_cores: String(values.oversell_cores),
          pool_label: pool,
          vcpu: values.vcpu,
          mem_gb: values.mem_gb,
          disk_gb: values.disk_gb,
          price_hourly: values.price_hourly,
          cuda_max: values.cuda_max ?? null,
          period_enabled: values.period_enabled,
          spot_enabled: values.spot_enabled,
        };
        create.mutate({ data: createPayload });
      } else if (editing) {
        // 型号不可改(SkuUpdate 无该字段)
        const { gpu_model, ...gpuUpdatable } = gpuFields;
        void gpu_model;
        const updatePayload: SkuUpdate = {
          name: values.name,
          ...gpuUpdatable,
          oversell_cores: String(values.oversell_cores),
          pool_label: pool,
          vcpu: values.vcpu,
          mem_gb: values.mem_gb,
          disk_gb: values.disk_gb,
          price_hourly: values.price_hourly,
          cuda_max: values.cuda_max ?? null,
          period_enabled: values.period_enabled,
          spot_enabled: values.spot_enabled,
          reason: values.reason ?? "",
        };
        update.mutate({ skuId: editing.id, data: updatePayload });
      }
    };
    // 改价二次确认(带影响预览)
    if (record === null || Number(values.price_hourly) === Number(record.price_hourly)) {
      doSubmit();
      return;
    }
    // L2 确认(useConfirm):变更行 + 影响面 + 范围说明;影响面查询在途时禁点确认
    confirm({
      title: t("skus.submitConfirmTitle"),
      consequences: [
        t("skus.priceChangeLine", {
          from: formatHourlyPrice(record.price_hourly),
          to: formatHourlyPrice(values.price_hourly),
        }),
        <span key="impact" style={{ color: adminColors.alertAccent }}>
          {impact.data
            ? t("skus.priceChangeImpact", {
                instances: impact.data.active_instances,
                users: impact.data.active_users,
                gpus: impact.data.active_gpus,
              })
            : t("skus.priceChangeImpactPending")}
        </span>,
      ],
      impact: t("skus.priceChangeScope"),
      okText: t("skus.confirmSubmit"),
      okDisabled: impact.isPending,
      onOk: doSubmit,
    });
  };

  const isNew = editing === "new";
  // 新建按选中集群资源限定池;编辑只放行同 tier 变体
  const variantOptions: SkuVariant[] = isNew
    ? (clusterPick?.pool_label ? (POOL_VARIANTS[clusterPick.pool_label] ?? ALL_VARIANTS) : ALL_VARIANTS)
    : ALL_VARIANTS.filter((v) => VARIANT_SPEC[v].tier === record?.tier);
  // 在售规格改池后端 409,先灰置并说明
  const variantLocked = !isNew && record?.status === "on";

  return (
    <PageContainer
      width="full"
      title={t("menu.skus")}
      extra={
        <Tooltip title={writable ? "" : t("common.readonlyNoCreate")}>
          <Button type="primary" disabled={!writable} onClick={() => openEdit("new")}>
            {t("skus.newSku")}
          </Button>
        </Tooltip>
      }
    >
    <Card>
      <Table<SkuAdminOut>
        scroll={{ x: 1440 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
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
        dataSource={skus ?? []}
        pagination={false}
        columns={[
          { title: t("skus.colName"), dataIndex: "name", fixed: "left", width: 200 },
          { title: t("skus.colGpuModel"), dataIndex: "gpu_model" },
          {
            title: t("skus.colTier"),
            render: (_, r) => {
              const v = skuVariant(r.tier, r.pool_label);
              const m = metaOf(skuTierMap, v);
              return <HexTag color={m?.color}>{m ? t(m.labelKey) : v}</HexTag>;
            },
          },
          {
            title: t("skus.colSlice"),
            render: (_, r) =>
              r.tier === "cpu"
                ? "—"
                : r.pool_label === "mig"
                  ? r.mig_profile
                  : t("skus.sliceShared", { pct: r.gpu_cores_pct, vram: r.vram_gb }),
          },
          {
            // 容量 = 匹配型号×池的物理卡数;CPU 规格不带卡
            title: t("skus.colCapacity"),
            dataIndex: "capacity_gpus",
            render: (v: number, r) =>
              r.tier === "cpu" ? "—" : v === 0 && r.status === "on" ? <Tag color="red">0</Tag> : v,
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
          { title: t("skus.colPrice"), dataIndex: "price_hourly", render: (v: string) => formatHourlyPrice(v) },
          {
            title: t("skus.colPeriod"),
            dataIndex: "period_enabled",
            width: 100,
            render: (v: boolean) =>
              v ? <Tag color="blue">{t("skus.periodOn")}</Tag> : <Tag>{t("skus.periodOff")}</Tag>,
          },
          {
            title: t("skus.colSpot"),
            dataIndex: "spot_enabled",
            width: 100,
            render: (v: boolean) =>
              v ? <Tag color="orange">{t("skus.spotOn")}</Tag> : <Tag>{t("skus.spotOff")}</Tag>,
          },
          {
            title: t("skus.colOnSale"),
            dataIndex: "status",
            render: (v: string, r) =>
              v === "on" ? (
                <ReasonAction
                  label={t("skus.offSale")}
                  target={r.name}
                  danger
                  title={t("skus.offSaleTitle")}
                  confirmText={t("skus.offSaleConfirm", { name: r.name })}
                  disabled={!writable}
                  disabledReason={t("nodes.readonlyNoOp")}
                  onSubmit={async (reason) => {
                    await offSale.mutateAsync({
                      skuId: r.id,
                      data: { status: "off", reason },
                    });
                  }}
                />
              ) : (
                // 上架:规格缺要素被拒时给「强制上架」出口
                <ReasonAction
                  label={t("skus.onSale")}
                  target={r.name}
                  title={t("skus.onSaleTitle")}
                  confirmText={t("skus.onSaleConfirm", { name: r.name })}
                  disabled={!writable}
                  disabledReason={t("nodes.readonlyNoOp")}
                  onSubmit={async (reason) => {
                    try {
                      await onSale.mutateAsync({
                        skuId: r.id,
                        data: { status: "on", reason },
                      });
                    } catch (e) {
                      // SKU_NOT_SELLABLE:确认后带 force 重放;其他错误继续抛给 ReasonAction
                      if (isApiError(e) && e.code === "SKU_NOT_SELLABLE") {
                        confirm({
                          title: t("skus.notSellableTitle"),
                          consequences: [errText(e, t("skus.toggleFailed"))],
                          okText: t("skus.forceOn"),
                          danger: true,
                          onOk: () =>
                            forceOnSale.mutate({
                              skuId: r.id,
                              data: { status: "on", reason },
                              force: true,
                            }),
                        });
                      }
                      throw e;
                    }
                  }}
                />
              ),
          },
          {
            title: t("skus.colActions"),
            fixed: "right",
            width: 90,
            render: (_, r) => (
              <Tooltip title={writable ? "" : t("common.readonlyNoEdit")}>
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
        width="min(760px, 100vw)"
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={submit}>
            {t("skus.submit")}
          </Button>
        }
      >
        <div style={{ display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
          <Form
            form={form}
            layout="vertical"
            style={{ flex: 1, minWidth: 0 }}
            onValuesChange={() => {
              if (isNew) draft.save(form.getFieldsValue(true) as Partial<SkuFormValues>);
            }}
          >
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
                title={t("skus.clusterEmptyHint")}
              />
            )}
            <Form.Item name="name" label={t("skus.colName")} rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            {/* 型号只在新建时出现;CPU 规格不带型号 */}
            {isNew && !isCpuVariant && (
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
            )}
            {/* 档位与切片编辑态也挂载 */}
            <Form.Item
              name="variant"
              label={t("skus.colTier")}
              rules={[{ required: true }]}
              extra={variantLocked ? t("skus.tierLockedOnSale") : undefined}
            >
              <Select
                disabled={variantLocked}
                onChange={onVariantChange}
                options={variantOptions.map((v) => ({
                  value: v,
                  label: t(skuTierMap[v].labelKey),
                }))}
              />
            </Form.Item>
            {wVariant === "shared_mig" && (
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
            {/* 池由档位派生;CPU 档可选 cpu / hami */}
            <Form.Item
              name="pool_label"
              label={t("nodes.poolLabel")}
              rules={[{ required: true }]}
              extra={isCpuVariant ? t("skus.cpuPoolHint") : undefined}
            >
              <Select
                disabled={!isCpuVariant}
                options={Object.entries(POOL_LABEL_KEY)
                  .filter(([value]) => !isCpuVariant || value === "cpu" || value === "hami")
                  .map(([value, labelKey]) => ({ value, label: t(labelKey) }))}
              />
            </Form.Item>
            {/* CPU 规格不挂载,提交时由 CPU_ZERO_FIELDS 补零 */}
            {!isCpuVariant && (
              <>
                <Form.Item
                  name="gpu_cores_pct"
                  label={t("skus.coresPctLabel")}
                  rules={[{ required: true }]}
                >
                  <InputNumber
                    min={1}
                    max={100}
                    disabled={!!wVariant && wVariant !== "shared_hami"}
                    style={{ width: "100%" }}
                    onChange={(v) => {
                      if (clusterPick && wVariant && typeof v === "number") {
                        applyRecommend(clusterPick, wVariant, v);
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
                  title={t("skus.oversellRisk")}
                  description={t("skus.oversellRiskDesc")}
                />
                <Form.Item
                  name="oversell_cores"
                  label={t("skus.oversellCoresLabel")}
                  rules={[{ required: true }]}
                >
                  <InputNumber min={1} max={9.99} step={0.1} style={{ width: "100%" }} />
                </Form.Item>
              </>
            )}
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
            {/* 包周期开关:关掉只挡新单 */}
            <Form.Item
              name="period_enabled"
              label={t("skus.periodEnabledLabel")}
              valuePropName="checked"
              extra={t("skus.periodEnabledHint")}
            >
              <Switch />
            </Form.Item>
            {/* 竞价开关:关掉只挡新单 */}
            <Form.Item
              name="spot_enabled"
              label={t("skus.spotEnabledLabel")}
              valuePropName="checked"
              extra={t("skus.spotEnabledHint")}
            >
              <Switch />
            </Form.Item>
            {/* 编辑必填原因(入审计) */}
            {editing !== "new" && (
              <Form.Item
                name="reason"
                label={t("skus.reasonLabel")}
                rules={[{ required: true, min: 2, max: REASON_MAX_LEN, message: t("skus.reasonRequired") }]}
              >
                <Input.TextArea rows={2} placeholder={t("skus.reasonPlaceholder")} />
              </Form.Item>
            )}
            {!isCpuVariant && (
              <Form.Item name="max_gpus_per_instance" label={t("skus.maxGpusLabel")}>
                <InputNumber min={1} max={8} style={{ width: "100%" }} />
              </Form.Item>
            )}
            {/* 最高 CUDA:CPU 档不出现 */}
            {!isCpuVariant && (
              <Form.Item name="cuda_max" label={t("skus.cudaMaxLabel")}>
                <Input placeholder={t("images.cudaPlaceholder")} />
              </Form.Item>
            )}
          </Form>
          <Card size="small" title={t("skus.previewTitle")} style={{ width: 248, flexShrink: 0 }}>
            {previewParams === null ? (
              <Typography.Text type="secondary">{t("skus.previewPending")}</Typography.Text>
            ) : preview.data ? (
              <>
                {(isCpuVariant
                  ? [
                      [t("skus.previewNodes"), preview.data.matching_nodes],
                      [t("skus.previewEst"), preview.data.est_instances],
                    ]
                  : [
                      [t("skus.previewNodes"), preview.data.matching_nodes],
                      [t("skus.previewReadyGpus"), preview.data.ready_gpus],
                      [t("skus.previewTotalGpus"), preview.data.total_gpus],
                      [t("skus.previewEst"), preview.data.est_instances],
                    ]
                ).map(([label, value]) => (
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
                    title={warnText(t, w)}
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
    </PageContainer>
  );
}
