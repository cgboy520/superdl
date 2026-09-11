/** 可用量去重聚合:按 (pool_label, gpu_model) 分组取 max,再跨组求和。 */

import type { SkuMarketOut } from "@superdl/api-client";

/** 入参即 SkuMarketOut,只取去重所需三字段。 */
export type SkuAvailabilityLike = Pick<SkuMarketOut, "pool_label" | "gpu_model" | "available_count">;

/** 全局可售上限(去重后):首页/CTA 横幅数字。 */
export function dedupAvailableTotal(skus: readonly SkuAvailabilityLike[]): number {
  let total = 0;
  for (const v of dedupAvailableByModel(skus).values()) total += v;
  return total;
}

/** 按型号聚合的可售数(市场页型号 chip):组内 (池, 型号) 取 max,同型号跨池求和;CPU 规格(gpu_model 空串)整条跳过。 */
export function dedupAvailableByModel(skus: readonly SkuAvailabilityLike[]): Map<string, number> {
  const byGroup = new Map<string, { model: string; free: number }>();
  for (const s of skus) {
    if (!s.gpu_model) continue;
    const key = `${s.pool_label ?? ""}${s.gpu_model}`;
    const free = s.available_count ?? 0;
    const cur = byGroup.get(key);
    if (cur === undefined || free > cur.free) {
      byGroup.set(key, { model: s.gpu_model, free });
    }
  }
  const byModel = new Map<string, number>();
  for (const { model, free } of byGroup.values()) {
    byModel.set(model, (byModel.get(model) ?? 0) + free);
  }
  return byModel;
}
