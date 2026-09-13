/** SKU 新建 / 编辑抽屉:集群资源联动(推荐填表)+ 容量预览 + 改价影响面确认;表单常量与联动纯函数在 -skuForm。 */

import { App, Alert, Button, Card, Drawer, Form, Input, InputNumber, Select, Spin, Switch, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { adminColors, drawerWidth, skuTierMap, skuVariant, type SkuVariant } from "@superdl/ui";
import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";
import { useConfirm } from "@superdl/ui/components";
import { useFormDraft } from "@superdl/ui";

import {
  type GpuModelAggregate,
  type SkuAdminOut,
  useCreateSku,
  useGpuModelAggregates,
  useSkuCapacityPreview,
  useSkuImpact,
  useUpdateSku,
} from "../../api";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { REASON_MAX_LEN } from "../../lib/validators";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import {
  ALL_VARIANTS,
  buildSkuPayload,
  CPU_ZERO_FIELDS,
  POOL_VARIANTS,
  recommendFields,
  type SkuFormValues,
  useWatchSkuField,
  VARIANT_SPEC,
  warnText,
} from "./-skuForm";

export function SkuDrawerForm({
  editing,
  onClose,
  onSaved,
}: {
  /** "new" = 新建;null = 关闭 */
  editing: SkuAdminOut | "new" | null;
  onClose: () => void;
  /** 保存成功后(列表刷新) */
  onSaved: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatHourlyPrice } = useFormat();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const role = useAdminRole();
  // 聚合端点只放 ops/readonly,按角色关停查询
  const { data: aggregates } = useGpuModelAggregates({
    enabled: canWriteOps(role) || role === "readonly",
  });
  const [clusterPick, setClusterPick] = useState<GpuModelAggregate | null>(null);
  const [form] = Form.useForm<SkuFormValues>();
  // 新建草稿(sessionStorage);编辑态不写草稿
  const draft = useFormDraft<SkuFormValues>("sku-new");
  // 关闭即清集群选择(下次打开重新选;所有关闭路径都经这里)
  const handleClose = () => {
    setClusterPick(null);
    onClose();
  };

  const clusterOptions = useMemo(
    () => (aggregates ?? []).filter((a) => a.gpu_model && a.pool_label && POOL_VARIANTS[a.pool_label]),
    [aggregates],
  );

  const create = useCreateSku({
    mutation: {
      onSuccess: () => {
        message.success(t("skus.created"));
        draft.clear();
        handleClose();
        onSaved();
      },
      onError: (e) => message.error(errText(e, t("common.createFailed"))),
    },
  });
  const update = useUpdateSku({
    mutation: {
      onSuccess: () => {
        message.success(t("skus.saved"));
        handleClose();
        onSaved();
      },
      onError: (e) => message.error(errText(e, t("common.saveFailed"))),
    },
  });

  // 开合时初始化表单:新建 = 默认值 + 草稿覆盖;编辑 = 记录回显
  useEffect(() => {
    if (editing === null) return;
    if (editing === "new") {
      form.resetFields();
      form.setFieldsValue({
        variant: "shared_hami",
        gpu_cores_pct: 50,
        oversell_cores: 1.5,
        disk_gb: 100,
        max_gpus_per_instance: 1,
        pool_label: "hami",
        vcpu: 8,
        mem_gb: 32,
        // 与后端 SkuCreate.period_enabled 默认值一致
        period_enabled: true,
        // 与后端 SkuCreate.spot_enabled 默认值一致
        spot_enabled: false,
        // 草稿覆盖默认值(仅新建)
        ...draft.load(),
      });
    } else {
      form.setFieldsValue({
        ...editing,
        variant: skuVariant(editing.tier, editing.pool_label),
        oversell_cores: Number(editing.oversell_cores),
        price_hourly: editing.price_hourly,
      });
    }
  }, [editing, form, draft]);

  // 容量预览参数(编辑态型号/档位取自记录)
  const record = editing !== null && editing !== "new" ? editing : null;
  // 改价影响面(编辑态才查)
  const impact = useSkuImpact(record?.id ?? null);
  const wModel = useWatchSkuField(form, "gpu_model");
  const wVariant = useWatchSkuField(form, "variant");
  const wPool = useWatchSkuField(form, "pool_label");
  const wPct = useWatchSkuField(form, "gpu_cores_pct");
  const wOversell = useWatchSkuField(form, "oversell_cores");
  const wVram = useWatchSkuField(form, "vram_gb");
  const wVcpu = useWatchSkuField(form, "vcpu");
  const wMem = useWatchSkuField(form, "mem_gb");
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
    form.setFieldsValue(recommendFields(agg, variant, pct));
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
      applyRecommend(agg, variant, (form.getFieldValue("gpu_cores_pct") as number | undefined) ?? 50);
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
      applyRecommend(clusterPick, variant, (form.getFieldValue("gpu_cores_pct") as number | undefined) ?? 50);
    } else if (variant !== "shared_hami") {
      form.setFieldsValue({ gpu_cores_pct: 100 });
    }
  };

  const submit = async () => {
    try {
      await form.validateFields();
    } catch {
      return; // 校验失败:antd 已就地标红
    }
    // 取值用 getFieldsValue(true):validateFields() 只回已挂载 Form.Item 的字段
    const values = form.getFieldsValue(true) as SkuFormValues;
    const doSubmit = () => {
      const payload = buildSkuPayload(values, editing);
      if (payload?.kind === "create") create.mutate({ data: payload.data });
      else if (payload?.kind === "update") update.mutate({ skuId: payload.skuId, data: payload.data });
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
    ? clusterPick?.pool_label
      ? (POOL_VARIANTS[clusterPick.pool_label] ?? ALL_VARIANTS)
      : ALL_VARIANTS
    : ALL_VARIANTS.filter((v) => VARIANT_SPEC[v].tier === record?.tier);
  // 在售规格改池后端 409,先灰置并说明
  const variantLocked = !isNew && record?.status === "on";

  return (
    <Drawer
      title={isNew ? t("skus.newSku") : t("skus.editTitle", { name: record?.name ?? "" })}
      open={editing !== null}
      onClose={handleClose}
      size={drawerWidth.lg}
      extra={
        <Button type="primary" loading={create.isPending || update.isPending} onClick={() => void submit()}>
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
            <Alert type="info" showIcon style={{ marginBottom: 16 }} title={t("skus.clusterEmptyHint")} />
          )}
          <Form.Item name="name" label={t("skus.colName")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {/* 型号只在新建时出现;CPU 规格不带型号 */}
          {isNew && !isCpuVariant && (
            <Form.Item name="gpu_model" label={t("skus.gpuModelLabel")} rules={[{ required: true }]}>
              <Input placeholder={t("skus.gpuModelPlaceholder")} disabled={clusterPick !== null} />
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
            <Form.Item name="mig_profile" label={t("skus.migProfileLabel")} rules={[{ required: true }]}>
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
              <Form.Item name="gpu_cores_pct" label={t("skus.coresPctLabel")} rules={[{ required: true }]}>
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
                <InputNumber min={1} max={clusterPick?.vram_gb || undefined} style={{ width: "100%" }} />
              </Form.Item>
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                title={t("skus.oversellRisk")}
                description={t("skus.oversellRiskDesc")}
              />
              <Form.Item name="oversell_cores" label={t("skus.oversellCoresLabel")} rules={[{ required: true }]}>
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
                <div key={String(label)} style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <Typography.Text type="secondary">{label}</Typography.Text>
                  <Typography.Text strong>{value}</Typography.Text>
                </div>
              ))}
              {preview.data.warnings.map((w) => (
                <Alert key={w.code} type="warning" showIcon style={{ marginTop: 8 }} title={warnText(t, w)} />
              ))}
            </>
          ) : (
            <Spin size="small" />
          )}
        </Card>
      </div>
    </Drawer>
  );
}
