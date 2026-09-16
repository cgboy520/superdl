/** Deploy / create deep-link prefill: spec / card count / billing mode; spot and periods exclude each other, period wins. Shared by /services/new and /market/create/:skuId (the latter carries sku in the path, the caller drops sku_id). */

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
    const c = Number(search.count);
    if (Number.isInteger(c) && c >= 1 && c <= MAX_PERIOD_COUNT) out.count = c;
  } else if (search.market === "spot") out.market = "spot";
  return out;
}
