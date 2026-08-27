import { adminColors, metaOf, skuTierMap, skuVariant, type SkuTier, type SkuVariant } from "@superdl/ui";
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
import { useFormat } from "../../lib/format";
import { useApiErrorText } from "../../lib/apiError";
import { useFormDraft } from "../../lib/formDraft";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { StatusTag } from "../../components/StatusTag";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/skus")({
  component: SkusPage,
});

interface SkuFormValues {
  name: string;
  gpu_model: string;
  /** 表单只让运营选「展示档位」,提交时派生出 tier 与 pool_label —— 两者能对不齐就是事故。
   *  tier 刻意不做表单字段:antd 的 validateFields() 只回已挂载 Form.Item 的值,
   *  靠 setFieldsValue 塞进 store 的字段拿不到(会静默漏字段)。 */
  variant: SkuVariant;
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
  /** 编辑必填(入审计);新建端点不接受 reason,提交时不带 */
  reason?: string;
}

// 展示档位 → (落库档位, 节点池)。隔离机制的事实源是池,档位只是售卖名字;
// 让运营只选前者、后两者派生,是「卖的隔离强度 = 实际跑的」的唯一保证(后端 catalog
// 的 _check_tier_pool 是同一份约束的服务端版本)。
const VARIANT_SPEC: Record<SkuVariant, { tier: SkuTier; pool: string }> = {
  dedicated: { tier: "dedicated", pool: "kata" },
  shared_mig: { tier: "shared", pool: "mig" },
  shared_hami: { tier: "shared", pool: "hami" },
  // CPU 档默认落 cpu 池(无卡机);要跑 GPU 机的空闲 CPU 就把池改成 hami,
  // 后端 TIER_POOLS 两者都放行,这里只给默认值
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
/** CPU 规格必须落库的 GPU 字段值(后端 catalog.cpu_spec_error 的镜像:任一非 0 即被拒) */
/** CPU 规格必须落库的值(后端 catalog.cpu_spec_error 的镜像:GPU 三项任一非 0 即被拒)。
 *  超卖也一并钉成 1:CPU 规格没有算力/显存可超卖,而那两个输入框在 cpu 档不挂载 ——
 *  不显式覆盖就会把切档前的旧值(默认 1.5)带进库,管理端超卖列显示成 1.50× 纯属误导。 */
const CPU_ZERO_FIELDS = {
  gpu_model: "",
  mig_profile: null,
  gpu_cores_pct: 0,
  vram_gb: 0,
  max_gpus_per_instance: 0,
  oversell_cores: 1,
  oversell_vram: 1,
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
  const { data: skus, queryKey } = useAdminSkus();
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
        const code = isApiError(e) ? e.code : undefined;
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
  // 折算口径只看池(超卖只发生在 HAMi),端点因此不再收 tier。
  // CPU 规格没有型号:留空 gpu_model 让端点走 vCPU/内存上限口径,别拿空型号去报「未识别」
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
      // cpu 档在 cpu / hami 两池都合法,但默认必须是 cpu 池:挂 hami 是去吃 GPU 机的空闲 CPU,
      // 要受 gpu_node_cpu_instance_vcpu_cap 封顶、也会挤占 GPU 实例的配套 CPU ——
      // 影响更大的那个选项不该是静默默认值,得让运营显式改
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
        variant: "shared_hami", gpu_cores_pct: 50, oversell_cores: 1.5, oversell_vram: 1.0,
        disk_gb: 100, max_gpus_per_instance: 1, pool_label: "hami", vcpu: 8, mem_gb: 32,
        // 草稿覆盖默认值(仅新建):误关抽屉后重开不丢
        ...draft.load(),
      });
    } else {
      form.setFieldsValue({
        ...sku,
        variant: skuVariant(sku.tier, sku.pool_label),
        oversell_cores: Number(sku.oversell_cores),
        oversell_vram: Number(sku.oversell_vram),
        price_hourly: sku.price_hourly,
      });
    }
  };

  const submit = async () => {
    await form.validateFields();
    // 取全量 store 而非 validateFields() 的返回值:后者只回**已挂载** Form.Item 的字段,
    // 而本表单按档位隐藏大半输入框(cpu 档没有型号/显存/份额/超卖/单卡数,非 mig 档没有切片)。
    // 读返回值会让那些字段变成 undefined 混进 payload —— String(undefined) 就是 "undefined",
    // 服务端直接 422。这个坑在本文件已经踩过三次,统一走 store。
    const values = form.getFieldsValue(true) as SkuFormValues;
    // 派生而非读表单:tier 没有 Form.Item,pool_label 的输入框是只读回显,
    // 两者的事实源都是 variant
    const { tier, pool: derivedPool } = VARIANT_SPEC[values.variant];
    // 只有 cpu 档的池是运营可选的(cpu 池 / 蹭 GPU 节点的 hami 池),其余三档恒由档位派生。
    // 不写成 `values.pool_label || derivedPool`:那个 || 永远不会触发(池的 Form.Item 一直挂载,
    // 禁用不等于不挂载),留着只会让人以为这里有回落逻辑
    const pool = values.variant === "cpu" ? values.pool_label : derivedPool;
    // CPU 规格的 GPU 字段全部清零:那几个 Form.Item 在 cpu 档不挂载,validateFields()
    // 拿不到它们的值(antd 只回已挂载项),不显式补零会漏字段
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
          oversell_vram: String(values.oversell_vram),
          pool_label: pool,
          vcpu: values.vcpu,
          mem_gb: values.mem_gb,
          disk_gb: values.disk_gb,
          price_hourly: values.price_hourly,
          cuda_max: values.cuda_max ?? null,
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
          oversell_vram: String(values.oversell_vram),
          pool_label: pool,
          vcpu: values.vcpu,
          mem_gb: values.mem_gb,
          disk_gb: values.disk_gb,
          price_hourly: values.price_hourly,
          cuda_max: values.cuda_max ?? null,
          reason: values.reason ?? "",
        };
        update.mutate({ skuId: editing.id, data: updatePayload });
      }
    };
    // 改价二次确认(带影响预览:当前在跑台数/涉及用户);显存超卖 >1.2 同框复用
    const priceChanged =
      record !== null && Number(values.price_hourly) !== Number(record.price_hourly);
    const vramHigh = values.oversell_vram > 1.2;
    if (!priceChanged && !vramHigh) {
      doSubmit();
      return;
    }
    modal.confirm({
      title: t("skus.submitConfirmTitle"),
      content: (
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {vramHigh && <span>{t("skus.vramOversellConfirmBody")}</span>}
          {priceChanged && record && (
            <>
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
              <span style={{ color: adminColors.textSecondary, fontSize: 12 }}>
                {t("skus.priceChangeScope")}
              </span>
            </>
          )}
        </Space>
      ),
      okText: t("skus.confirmSubmit"),
      okButtonProps: { danger: vramHigh },
      onOk: doSubmit,
    });
  };

  const isNew = editing === "new";
  // 新建:按选中的集群资源限定池;编辑:tier 不可改(SkuUpdate 无该字段),
  // 只放行同 tier 的变体 —— 即「共享」在 mig / hami 两池之间改挂
  const variantOptions: SkuVariant[] = isNew
    ? (clusterPick?.pool_label ? (POOL_VARIANTS[clusterPick.pool_label] ?? ALL_VARIANTS) : ALL_VARIANTS)
    : ALL_VARIANTS.filter((v) => VARIANT_SPEC[v].tier === record?.tier);
  // 改档位就是改池,在售规格后端 409(换池 = 换商品);这里先灰置并说明,不让人白填一遍
  const variantLocked = !isNew && record?.status === "on";

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
            render: (_, r) => {
              const v = skuVariant(r.tier, r.pool_label);
              const m = metaOf(skuTierMap, v);
              return <StatusTag color={m?.color}>{m ? t(m.labelKey) : v}</StatusTag>;
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
                  // 按行隔离:mutation 级 isPending 会让全表开关一起转,看着像批量生效
                  loading={toggleSale.isPending && toggleSale.variables?.skuId === r.id}
                  onChange={(on) =>
                    toggleSale.mutate({
                      skuId: r.id,
                      data: { status: on ? "on" : "off", reason: t("skus.reasonToggle") },
                    })
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
            {/* 档位与切片编辑态也要在:下架规格可在 mig / hami 两池之间改挂。
                关进 isNew 会让改池不可达,且编辑时 mig_profile 不挂载 = 提交被抹成 null */}
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
            {/* 池恒由上面的档位派生,不单独可改 —— 两者能各改各的就会卖错隔离强度。
                唯一例外是 CPU 档:它在 cpu(无卡机)与 hami(GPU 机的空闲 CPU)两池都合法,
                两者对用户无差别、只影响落在哪批机器上,交由运营选(后端 TIER_POOLS 同样放行) */}
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
            {/* 算力份额/显存/超卖三项都是卡的属性:CPU 规格整块不挂载,提交时由
                CPU_ZERO_FIELDS 补零(超卖两列保留 DB 默认 1.00,对不带卡的规格无意义) */}
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
                <Form.Item
                  name="oversell_vram"
                  label={t("skus.oversellVramLabel")}
                  rules={[{ required: true }]}
                >
                  <InputNumber min={1} max={9.99} step={0.05} style={{ width: "100%" }} />
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
            {/* 编辑必填原因:新实例会永久快照当时单价,审计只记新值就答不出「从多少改到多少」 */}
            {editing !== "new" && (
              <Form.Item
                name="reason"
                label={t("skus.reasonLabel")}
                rules={[{ required: true, min: 2, max: 200, message: t("skus.reasonRequired") }]}
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
  );
}
