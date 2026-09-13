/** 财务页共用:Tab 白名单、URL 筛选态解析与读取(各 Tab 从这里取 ?tab= 之外的筛选参数)。 */

import { useNavigate, getRouteApi } from "@tanstack/react-router";

const routeApi = getRouteApi("/_app/finance");

// 审计独立成页(/audit),财务页不再内嵌
export const FINANCE_TABS = ["orders", "refunds", "invoices", "adjustments", "gaps", "anomalies"] as const;
export type FinanceTab = (typeof FINANCE_TABS)[number];

// URL 筛选白名单:状态取共享映射表,日期 YYYY-MM-DD,账期 YYYY-MM,租户 id 正整数
export const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
export const PERIOD_RE = /^\d{4}-\d{2}$/;
// 结算缺口类型(与 AdminListSettlementGapsApiAdminV1FinanceSettlementGapsGetKind 一致)
export const GAP_KINDS = ["hourly", "daily_disk"] as const;
export type GapKind = (typeof GAP_KINDS)[number];

export interface FinanceSearch {
  tab?: FinanceTab;
  // 日对账卡的对账日(YYYY-MM-DD;缺省 = 今天)
  day?: string;
  // 订单 Tab
  o_status?: string;
  o_no?: string;
  o_day?: string;
  // 退款 Tab
  r_status?: string;
  r_day?: string;
  r_channel?: string;
  // 发票 Tab
  i_status?: string;
  i_period?: string;
  // 调账 Tab
  a_status?: string;
  a_day?: string;
  a_uid?: number;
  // 结算缺口 Tab:类型;g_open="0" = 含已核销(默认只看未核销,默认值不入 URL)
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
