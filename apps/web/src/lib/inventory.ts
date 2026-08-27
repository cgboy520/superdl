/** 可用量去重聚合:同一 (池, 型号) 物理池上的互斥规格(50% / 30% 算力份额等)可售数
 * 不可相加——32 张物理卡会被两个规格各自折算成 96/192 台「实例」,直接 sum 高估 43%。
 * 口径:按 (pool_label, gpu_model) 分组取 max,再跨组求和。
 */

export interface SkuAvailabilityLike {
  pool_label?: string | undefined;
  gpu_model: string;
  available_count?: number | undefined;
}

/** 全局可售上限(去重后):首页/CTA 横幅的对外数字。 */
export function dedupAvailableTotal(skus: readonly SkuAvailabilityLike[]): number {
  let total = 0;
  for (const v of dedupAvailableByModel(skus).values()) total += v;
  return total;
}

/** 按型号聚合的可售数(市场页型号筛选 chip):组内 (池, 型号) 取 max,同型号跨池求和。 */
export function dedupAvailableByModel(skus: readonly SkuAvailabilityLike[]): Map<string, number> {
  const byGroup = new Map<string, { model: string; free: number }>();
  for (const s of skus) {
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
