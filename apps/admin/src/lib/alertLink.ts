/** 告警跳转目标(总览告警流与顶栏铃铛共用,后端按现有字段派生 target_kind/target_id):
 *  无 target 不可点;node/ticket 深链带目标 id,由目标页消费(选中高亮/自动开抽屉)。 */

import type { AlertRow } from "../api";

/** 级别码 → 文案键(静态表:admin 的 t() 是严格键类型,裸级别码 label 不进 t());总览告警流与告警中心共用。 */
export const SEVERITY_LABEL_KEY = {
  info: "overview.severityInfo",
  warning: "overview.severityWarning",
  critical: "overview.severityCritical",
} as const;

export function alertLink(a: AlertRow): { to: string; search?: Record<string, string | number> } | null {
  if (a.target_kind === "tenant" && a.target_id) {
    return { to: "/tenants", search: { q: a.target_id } };
  }
  if (a.target_kind === "node" && a.target_id) return { to: "/nodes", search: { node: a.target_id } };
  if (a.target_kind === "ticket" && a.target_id) {
    return { to: "/tickets", search: { id: a.target_id } };
  }
  return null;
}
