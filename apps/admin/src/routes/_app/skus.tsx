import { adminColors, fontSize, metaOf, skuTierMap, skuVariant, type SkuTier, type SkuVariant } from "@superdl/ui";
import { HexTag, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
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
  Space,
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
  /** 表单只让运营选「展示档位」,提交时派生出 tier 与 pool_label。
   *  tier 不做表单字段:antd validateFields() 只回已挂载 Form.Item 的值,setFieldsValue 塞进去的会静默漏。 */
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
  /** 是否接受包周期(预付)下单。与档位正交,是同一条 SKU 的另一种买法 */
  period_enabled: boolean;
  /** 是否上竞价档(可被平台回收换取折扣)。与包周期同级,仍是另一种买法 */
  spot_enabled: boolean;
  /** 编辑必填(入审计);新建端点不接受 reason,提交时不带 */
  reason?: string;
}

// 展示档位 →(落库档位, 节点池):隔离机制的事实源是池,运营只选档位;后端 catalog._check_tier_pool 同款约束。
const VARIANT_SPEC: Record<SkuVariant, { tier: SkuTier; pool: string }> = {
  dedicated: { tier: "dedicated", pool: "kata" },
  shared_mig: { tier: "shared", pool: "mig" },
  shared_hami: { tier: "shared", pool: "hami" },
  // CPU 档默认落 cpu 池(无卡机);改挂 hami 是去吃 GPU 机的空闲 CPU,后端 TIER_POOLS 两者都放行
  cpu: { tier: "cpu", pool: "cpu" },
};
const POOL_VARIANTS: Record<string, SkuVariant[]> = {
  kata: ["dedicated"],
  mig: ["shared_mig"],
  // hami 池上既能卖共享卡,也能卖只吃空闲 CPU 的 CPU 规格
  hami: ["shared_hami", "cpu"],
  cpu: ["cpu"],
};
const ALL_VARIANTS = Object.keys(VARIANT_SPEC) as SkuVariant[];
/** CPU 规格必须落库的值(后端 catalog.cpu_spec_error 的镜像:GPU 三项任一非 0 即被拒)。
 *  超卖一并钉成 1:那个输入框在 cpu 档不挂载,不显式覆盖会把切档前的旧值带进库。 */
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
  // 深色主题下必须走 useApp 实例:静态 message 拿不到 ConfigProvider token
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data: skus, queryKey, isLoading, isError, error, refetch } = useAdminSkus();
  // 聚合端点只放 ops/readonly:finance 可看 SKU 页但拉它会 403,按角色关停查询
  const { data: aggregates } = useGpuModelAggregates({
    enabled: canWriteOps(role) || role === "readonly",
  });
  const [editing, setEditing] = useState<SkuAdminOut | "new" | null>(null);
  const [clusterPick, setClusterPick] = useState<GpuModelAggregate | null>(null);
  const [form] = Form.useForm<SkuFormValues>();
  // 新建草稿(sessionStorage):误关抽屉/刷新不丢;编辑态不写草稿(避免跨记录串值)
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
  // 上架独立 mutation:不带 onError,错误提示统一由 ReasonAction 弹出(避免与 mutation 回调双提示)
  const onSale = useUpdateSku({
    mutation: { onSuccess: refresh },
  });
  // 强制上架独立 mutation:modal.confirm 流程自带成功/失败提示(与 ReasonAction 流程并行)
  const forceOnSale = useUpdateSku({
    mutation: {
      onSuccess: refresh,
      onError: (e) => message.error(errText(e, t("skus.toggleFailed"))),
    },
  });

  // 下架独立 mutation:不带 onError,错误提示统一由 ReasonAction 弹出(避免与 mutation 回调双提示)
  const offSale = useUpdateSku({
    mutation: { onSuccess: refresh },
  });

  // 表单联动:实时容量预览参数(编辑态型号/档位不在表单里,取自记录)
  const record = editing !== null && editing !== "new" ? editing : null;
  // 改价影响面(编辑态才查;新建无存量实例)
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
  // 折算口径只看池(超卖只发生在 HAMi),端点不收 tier。
  // CPU 规格必须留空 gpu_model 让端点走 vCPU/内存上限口径,否则会被报「未识别型号」
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
    // 只有 HAMi 软切分按算力份额折规格;整卡与 MIG 硬切分都拿整份(MIG 的份额由切片名定)
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
    // CPU 规格没有型号/显存可推荐:套上去会把 onVariantChange 刚清零的 GPU 字段再填回来
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
      // cpu 档在 cpu / hami 两池都合法,默认取 cpu 池;挂 hami 会挤占 GPU 实例的配套 CPU,需运营显式改
      pool_label: pool,
      // 切片只属于 mig 池:换走时必须清掉,否则后端 _check_tier_pool 会以「切片与池不符」驳回
      ...(variant === "shared_mig" ? {} : { mig_profile: null }),
    });
    if (variant === "cpu") {
      // GPU 字段一律清零:留着旧值提交会被后端 cpu_spec_error 拒掉,而那几个输入框此时已隐藏
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
        // 默认开:与后端 SkuCreate.period_enabled 默认值一致
        period_enabled: true,
        // 默认关:与后端 SkuCreate.spot_enabled 默认值一致
        spot_enabled: false,
        // 草稿覆盖默认值(仅新建):误关抽屉后重开不丢
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
    // 取值必须用 getFieldsValue(true) 而非 validateFields() 的返回值:后者只回已挂载 Form.Item 的字段,
    // 而本表单按档位隐藏大半输入框,漏掉的会变成 undefined 混进 payload(String(undefined) 即 "undefined")而 422。
    const values = form.getFieldsValue(true) as SkuFormValues;
    // tier / pool_label 派生而非读表单:tier 没有 Form.Item,池的输入框只是只读回显,事实源都是 variant
    const { tier, pool: derivedPool } = VARIANT_SPEC[values.variant];
    // 只有 cpu 档的池运营可选,其余三档恒由档位派生;池的 Form.Item 一直挂载(禁用不等于不挂载),
    // 写成 `values.pool_label || derivedPool` 那个 || 永远不会触发
    const pool = values.variant === "cpu" ? values.pool_label : derivedPool;
    // CPU 规格的 GPU 字段必须显式补零:那几个 Form.Item 在 cpu 档不挂载,不补就会漏字段
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
        // 新建端点不接受 reason(编辑才必填,入审计)
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
        // 型号不可改(SkuUpdate 无该字段),从 gpuFields 里摘掉
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
    // 改价二次确认(带影响预览:当前在跑台数/涉及用户)
    if (record === null || Number(values.price_hourly) === Number(record.price_hourly)) {
      doSubmit();
      return;
    }
    modal.confirm({
      title: t("skus.submitConfirmTitle"),
      content: (
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <span>
            {t("skus.priceChangeLine", {
              from: formatHourlyPrice(record.price_hourly),
              to: formatHourlyPrice(values.price_hourly),
            })}
          </span>
          <span style={{ color: adminColors.alertAccent }}>
            {impact.data
              ? t("skus.priceChangeImpact", {
                  instances: impact.data.active_instances,
                  users: impact.data.active_users,
                  gpus: impact.data.active_gpus,
                })
              : t("skus.priceChangeImpactPending")}
          </span>
          <span style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>
            {t("skus.priceChangeScope")}
          </span>
        </Space>
      ),
      okText: t("skus.confirmSubmit"),
      // 影响面查询在途时禁点确认:影响数字未到就放行,二次确认形同虚设
      okButtonProps: { disabled: impact.isPending },
      onOk: doSubmit,
    });
  };

  const isNew = editing === "new";
  // 新建按选中的集群资源限定池;编辑只放行同 tier 的变体(tier 不可改,SkuUpdate 无该字段)
  const variantOptions: SkuVariant[] = isNew
    ? (clusterPick?.pool_label ? (POOL_VARIANTS[clusterPick.pool_label] ?? ALL_VARIANTS) : ALL_VARIANTS)
    : ALL_VARIANTS.filter((v) => VARIANT_SPEC[v].tier === record?.tier);
  // 改档位就是改池,在售规格后端 409(换池 = 换商品);这里先灰置并说明
  const variantLocked = !isNew && record?.status === "on";

  return (
    <PageContainer
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
          { title: t("skus.colName"), dataIndex: "name" },
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
            // 容量列口径是「匹配型号×池的物理卡数」:CPU 规格不带卡,0 不是告警而是无此概念
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
            // 与档位正交,单独成列而不塞进档位标签
            title: t("skus.colPeriod"),
            dataIndex: "period_enabled",
            width: 100,
            render: (v: boolean) =>
              v ? <Tag color="blue">{t("skus.periodOn")}</Tag> : <Tag>{t("skus.periodOff")}</Tag>,
          },
          {
            // 与包周期同一口径,竞价档单独成列
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
                // 下架 = L2(手输原因 + 影响说明)
                <ReasonAction
                  label={t("skus.offSale")}
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
                // 上架同为 L2(手输原因,与下架同范式;弃用常量原因);规格缺要素被后端拒绝时给「强制上架」出口
                <ReasonAction
                  label={t("skus.onSale")}
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
                      // SKU_NOT_SELLABLE:后端给出缺要素清单,确认后带 force 重放(沿用本次手输原因);
                      // 错误继续抛出,由 ReasonAction 弹统一错误提示
                      if (isApiError(e) && e.code === "SKU_NOT_SELLABLE") {
                        modal.confirm({
                          title: t("skus.notSellableTitle"),
                          content: errText(e, t("skus.toggleFailed")),
                          okText: t("skus.forceOn"),
                          okButtonProps: { danger: true },
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
            {/* 型号不可改(SkuUpdate 无该字段),只在新建时出现;CPU 规格不带型号 */}
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
            {/* 档位与切片编辑态也必须挂载:关进 isNew 会让改池不可达,且 mig_profile 不挂载 = 提交被抹成 null */}
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
            {/* 池恒由档位派生,各改各的会卖错隔离强度;唯一例外是 CPU 档,cpu / hami 两池都合法交由运营选 */}
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
            {/* 算力份额/显存/超卖是卡的属性:CPU 规格整块不挂载,提交时由 CPU_ZERO_FIELDS 补零 */}
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
            {/* 包周期开关是定价的一部分,跟着单价放。关掉只挡新单,已在保的实例到期前仍占库存 */}
            <Form.Item
              name="period_enabled"
              label={t("skus.periodEnabledLabel")}
              valuePropName="checked"
              extra={t("skus.periodEnabledHint")}
            >
              <Switch />
            </Form.Item>
            {/* 竞价档同理。关掉只挡新单,已在跑的竞价实例仍可被回收、也仍可自行转按量 */}
            <Form.Item
              name="spot_enabled"
              label={t("skus.spotEnabledLabel")}
              valuePropName="checked"
              extra={t("skus.spotEnabledHint")}
            >
              <Switch />
            </Form.Item>
            {/* 编辑必填原因:新实例会永久快照当时单价,审计只记新值就答不出「从多少改到多少」 */}
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
            {/* 最高 CUDA 只对带卡的规格有意义,CPU 档不出现 */}
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
