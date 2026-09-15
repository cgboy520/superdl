/** 财务页共用:Tab 白名单、URL 筛选态解析与读取(各 Tab 从这里取 ?tab= 之外的筛选参数)。 */

import { useNavigate, getRouteApi } from "@tanstack/react-router";

const routeApi = getRouteApi("/_app/finance");

export const FINANCE_TABS = ["orders", "refunds", "invoices", "adjustments", "gaps", "anomalies"] as const;
export type FinanceTab = (typeof FINANCE_TABS)[number];

export const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
export const PERIOD_RE = /^\d{4}-\d{2}$/;
export const GAP_KINDS = ["hourly", "daily_disk"] as const;
export type GapKind = (typeof GAP_KINDS)[number];

export interface FinanceSearch {
  tab?: FinanceTab;
  day?: string;
  o_status?: string;
  o_no?: string;
  o_day?: string;
  r_status?: string;
  r_day?: string;
  r_channel?: string;
  i_status?: string;
  i_period?: string;
  a_status?: string;
  a_day?: string;
  a_uid?: number;
  g_kind?: GapKind;
  g_open?: "0";
}

/** 各 Tab 共用的 URL 筛选读写(replace,保留他项)。 */
export function useFinanceFilters() {
  const navigate = useNavigate({ from: "/finance" });
  const search = routeApi.useSearch();
  const setFilters = (next: Partial<FinanceSearch>) =>
    void navigate({
      to: "/finance",
      replace: true,
      search: (prev) => ({ ...prev, ...next }),
    });
  return { search, setFilters };
}
