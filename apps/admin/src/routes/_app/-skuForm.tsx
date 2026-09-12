/** SKU 表单事实源:表单值类型、档位 ↔ 池映射、CPU 规格清零字段、容量预警文案。 */

import { Form, type FormInstance } from "antd";
import { useTranslation } from "react-i18next";

import { type SkuTier, type SkuVariant } from "@superdl/ui";

import { type CapacityWarning } from "../../api";

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
