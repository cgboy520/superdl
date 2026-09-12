/** SKU 表单事实源:表单值类型、档位 ↔ 池映射、CPU 规格清零字段、容量预警文案。 */

import { Form, type FormInstance } from "antd";
import { useTranslation } from "react-i18next";

import { type SkuTier, type SkuVariant } from "@superdl/ui";

import {
  type CapacityWarning,
  type GpuModelAggregate,
  type SkuAdminOut,
  type SkuCreate,
  type SkuUpdate,
} from "../../api";

/** antd useWatch 的类型不含「字段未初始化」的 undefined,运行时会拿到;这里统一收窄出真实类型。 */
export function useWatchSkuField<K extends keyof SkuFormValues>(
  form: FormInstance<SkuFormValues>,
  name: K,
): SkuFormValues[K] | undefined {
  return Form.useWatch(name, form);
}

export interface SkuFormValues {
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
  price_hourly: string; // stringMode:单价 4 位小数,不经二进制浮点
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
export const VARIANT_SPEC: Record<SkuVariant, { tier: SkuTier; pool: string }> = {
  dedicated: { tier: "dedicated", pool: "kata" },
  shared_mig: { tier: "shared", pool: "mig" },
  shared_hami: { tier: "shared", pool: "hami" },
  // CPU 档默认 cpu 池,可改挂 hami(后端 TIER_POOLS 两者放行)
  cpu: { tier: "cpu", pool: "cpu" },
};
export const POOL_VARIANTS: Record<string, SkuVariant[]> = {
  kata: ["dedicated"],
  mig: ["shared_mig"],
  hami: ["shared_hami", "cpu"],
  cpu: ["cpu"],
};
export const ALL_VARIANTS = Object.keys(VARIANT_SPEC) as SkuVariant[];
/** CPU 规格提交时补零的 GPU 字段(镜像后端 catalog.cpu_spec_error);超卖钉成 1。 */
export const CPU_ZERO_FIELDS = {
  gpu_model: "",
  mig_profile: null,
  gpu_cores_pct: 0,
  vram_gb: 0,
  max_gpus_per_instance: 0,
  oversell_cores: 1,
} as const;

/** 「从集群资源创建」的推荐填表值:只有 HAMi 按算力份额折规格(pct%),整卡与 MIG 拿整份;agg 不带 vcpu/mem 时不写。 */
export function recommendFields(agg: GpuModelAggregate, variant: SkuVariant, pct: number): Partial<SkuFormValues> {
  const shared = variant === "shared_hami";
  const factor = shared ? pct / 100 : 1;
  return {
    gpu_model: agg.gpu_model ?? "",
    pool_label: agg.pool_label ?? "",
    gpu_cores_pct: shared ? pct : 100,
    vram_gb: Math.max(1, Math.floor(agg.vram_gb * factor)),
    ...(agg.vcpu_per_gpu ? { vcpu: Math.max(1, Math.round(agg.vcpu_per_gpu * factor)) } : {}),
    ...(agg.mem_gb_per_gpu ? { mem_gb: Math.max(1, Math.round(agg.mem_gb_per_gpu * factor)) } : {}),
  };
}

/** 提交负载派生:档位 →(tier, 池)(只有 cpu 档的池可选,其余由档位派生);CPU 档 GPU 字段补零;新建端点不接受 reason,编辑不带 gpu_model(型号不可改)。 */
export function buildSkuPayload(
  values: SkuFormValues,
  editing: SkuAdminOut | "new" | null,
): { kind: "create"; data: SkuCreate } | { kind: "update"; skuId: number; data: SkuUpdate } | null {
  if (editing === null) return null;
  const { tier, pool: derivedPool } = VARIANT_SPEC[values.variant];
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
  const base = {
    name: values.name,
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
  if (editing === "new") {
    return { kind: "create", data: { tier, ...gpuFields, ...base } };
  }
  // 型号不可改(SkuUpdate 无该字段)
  const { gpu_model, ...gpuUpdatable } = gpuFields;
  void gpu_model;
  return { kind: "update", skuId: editing.id, data: { ...base, ...gpuUpdatable, reason: values.reason ?? "" } };
}

export type TFn = ReturnType<typeof useTranslation<["admin", "shared"]>>["t"];

/** 告警 params 是后端自由 map({[key]: unknown});只接受字符串/数字,其余按缺失处理 */
const strParam = (v: unknown): string => (typeof v === "string" || typeof v === "number" ? String(v) : "");

export function warnText(t: TFn, w: CapacityWarning): string {
  const p = w.params ?? {};
  switch (w.code) {
    case "unrecognized_model":
      return t("skus.warnUnrecognizedModel", { model: strParam(p.model) });
    case "no_ready_node":
      return t("skus.warnNoReadyNode", {
        model: strParam(p.model),
        pool: strParam(p.pool),
      });
    case "vram_exceeds_node":
      return t("skus.warnVramExceedsNode", {
        vram: Number(p.vram_gb ?? 0),
        nodeVram: Number(p.node_vram_gb ?? 0),
      });
  }
}
