/** SKU form source of truth: form value types, tier ↔ pool mapping, CPU spec zeroed fields, capacity warning copy. */

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

/** Subscribe to form fields; the return type includes undefined before initialisation. */
export function useWatchSkuField<K extends keyof SkuFormValues>(
  form: FormInstance<SkuFormValues>,
  name: K,
): SkuFormValues[K] | undefined {
  return Form.useWatch(name, form);
}

export interface SkuFormValues {
  name: string;
  gpu_model: string;
  /** The form only picks the display tier; tier and pool_label are derived on submit, tier is not a form field. */
  variant: SkuVariant;
  mig_profile?: string | null;
  gpu_cores_pct: number;
  vram_gb: number;
  oversell_cores: number;
  pool_label: string;
  vcpu: number;
  mem_gb: number;
  disk_gb: number;
  price_hourly: string;
  max_gpus_per_instance: number;
  cuda_max?: string | null;
  /** Accepts subscription orders (orthogonal to the tier) */
  period_enabled: boolean;
  /** Listed in the spot tier (orthogonal to the tier) */
  spot_enabled: boolean;
  /** Required when editing (audited); the create endpoint rejects it */
  reason?: string;
}

export const VARIANT_SPEC: Record<SkuVariant, { tier: SkuTier; pool: string }> = {
  dedicated: { tier: "dedicated", pool: "kata" },
  shared_mig: { tier: "shared", pool: "mig" },
  shared_hami: { tier: "shared", pool: "hami" },
  cpu: { tier: "cpu", pool: "cpu" },
};
export const POOL_VARIANTS: Record<string, SkuVariant[]> = {
  kata: ["dedicated"],
  mig: ["shared_mig"],
  hami: ["shared_hami", "cpu"],
  cpu: ["cpu"],
};
export const ALL_VARIANTS = Object.keys(VARIANT_SPEC) as SkuVariant[];
/** GPU fields zeroed when submitting a CPU spec (mirrors the backend catalog.cpu_spec_error); oversell pinned to 1. */
export const CPU_ZERO_FIELDS = {
  gpu_model: "",
  mig_profile: null,
  gpu_cores_pct: 0,
  vram_gb: 0,
  max_gpus_per_instance: 0,
  oversell_cores: 1,
} as const;

/** Recommended values for "create from cluster resources": only HAMi scales the spec by compute share (pct%), whole cards and MIG take a full share; vcpu/mem are not written when agg lacks them. */
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

/** Submit payload derivation: tier → (tier, pool) (only the cpu tier lets the pool be chosen, the rest derive from the tier); CPU tier zeroes the GPU fields; the create endpoint rejects reason, edit omits gpu_model (the model cannot change). */
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
  const { gpu_model, ...gpuUpdatable } = gpuFields;
  void gpu_model;
  return { kind: "update", skuId: editing.id, data: { ...base, ...gpuUpdatable, reason: values.reason ?? "" } };
}

export type TFn = ReturnType<typeof useTranslation<["admin", "shared"]>>["t"];

/** Alert params is a free backend map ({[key]: unknown}); accept strings/numbers only, treat the rest as missing */
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
