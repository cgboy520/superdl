/** 部署/创建深链预填:规格 / 卡数 / 计费方式;竞价与包周期互斥,以 period 为准。/services/new 与 /market/create/:skuId 共用(后者 sku 在路径参数里,调用方丢弃 sku_id)。 */

import { isBillingPeriod, MAX_PERIOD_COUNT, type BillingPeriod } from "@superdl/ui";

export interface DeploySearch {
  sku_id?: number;
  gpus?: number;
  period?: BillingPeriod;
  market?: "spot";
  count?: number;
}

export function parseDeployDeepLink(search: Record<string, unknown>): DeploySearch {
  const out: DeploySearch = {};
  const sku = Number(search.sku_id);
  if (Number.isInteger(sku) && sku > 0) out.sku_id = sku;
  const g = Number(search.gpus);
  if (Number.isInteger(g) && g >= 1 && g <= 8) out.gpus = g;
  if (typeof search.period === "string" && isBillingPeriod(search.period)) {
    out.period = search.period;
    // 市场页购买时长透传(1~36)
    const c = Number(search.count);
    if (Number.isInteger(c) && c >= 1 && c <= MAX_PERIOD_COUNT) out.count = c;
  } else if (search.market === "spot") out.market = "spot";
  return out;
}
