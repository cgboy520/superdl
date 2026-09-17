/** Deduplicated availability aggregation: group by (pool_label, gpu_model), take the max per group, then sum across groups. */

import type { SkuMarketOut } from "@superdl/api-client";

/** Input is SkuMarketOut, only the three fields needed for deduplication. */
export type SkuAvailabilityLike = Pick<SkuMarketOut, "pool_label" | "gpu_model" | "available_count">;

/** Global sellable cap (deduplicated): the home / CTA banner figure. */
export function dedupAvailableTotal(skus: readonly SkuAvailabilityLike[]): number {
  let total = 0;
  for (const v of dedupAvailableByModel(skus).values()) total += v;
  return total;
}

/** Sellable count per model (market model chips): max per (pool, model) inside the group, summed across pools per model; CPU specs (empty gpu_model) are skipped. */
export function dedupAvailableByModel(skus: readonly SkuAvailabilityLike[]): Map<string, number> {
  const byGroup = new Map<string, { model: string; free: number }>();
  for (const s of skus) {
    if (!s.gpu_model) continue;
    const key = `${s.pool_label}${s.gpu_model}`;
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
