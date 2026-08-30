/** 告警跳转目标(总览告警流与顶栏铃铛共用,后端按现有字段派生 target_kind/target_id):
 *  无 target 不可点;node/ticket 深链带目标 id,由目标页消费(选中高亮/自动开抽屉)。 */

import { useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, useApiErrorText } from "@superdl/ui";

import { type AlertRow, useAckAlert } from "../api";

/** 级别码 → 文案键(静态表:admin 的 t() 是严格键类型);总览告警流与告警中心共用。 */
export const SEVERITY_LABEL_KEY = {
  info: "overview.severityInfo",
  warning: "overview.severityWarning",
  critical: "overview.severityCritical",
} as const;

/** 级别码 → 徽标色(三档表,铃铛/总览告警流/告警中心统一口径);未知级别回落中性灰蓝。 */
export function severityColor(severity: string): string {
  switch (severity) {
    case "critical":
      return adminColors.critical;
    case "warning":
      return adminColors.alertAccent;
    case "info":
      return adminColors.dataAccent;
    default:
      return adminColors.chartNeutral;
  }
}

/** 告警确认闭环(铃铛/总览告警流/告警中心同一范式):成功文案 + 失效 ["admin","alerts"] 前缀
 *  (前缀同时覆盖告警列表各参数化 queryKey 与 unread-count 角标);错误文案走后端 message_key。 */
export function useAckAlertWithFeedback() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  return useAckAlert({
    mutation: {
      onSuccess: () => {
        message.success(t("overview.ackDone"));
        void qc.invalidateQueries({ queryKey: ["admin", "alerts"] });
      },
      onError: (e) => message.error(errText(e, t("overview.ackFailed"))),
    },
  });
}

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
