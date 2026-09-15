/** 告警跳转目标(后端派生 target_kind/target_id):无 target 不可点;node/ticket 深链带目标 id。 */

import { useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "@superdl/ui";

import { adminKeys, type AlertRow, useAckAlert } from "../api";

/** 告警确认闭环:成功文案 + 失效 adminKeys.alerts 前缀;错误文案走后端 message_key。 */
export function useAckAlertWithFeedback() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  return useAckAlert({
    mutation: {
      onSuccess: () => {
        message.success(t("overview.ackDone"));
        void qc.invalidateQueries({ queryKey: adminKeys.alerts });
      },
      onError: (e) => message.error(errText(e, t("overview.ackFailed"))),
    },
  });
}

export function alertLink(a: AlertRow): { to: string; search?: Record<string, string | number> } | null {
  if (a.target_kind === "tenant" && a.target_id) {
    const id = Number(a.target_id);
    return {
      to: "/tenants",
      search: Number.isInteger(id) && id > 0 ? { q: a.target_id, tenant: id } : { q: a.target_id },
    };
  }
  if (a.target_kind === "node" && a.target_id) return { to: "/nodes", search: { node: a.target_id } };
  if (a.target_kind === "ticket" && a.target_id) {
    return { to: "/tickets", search: { id: a.target_id } };
  }
  return null;
}
