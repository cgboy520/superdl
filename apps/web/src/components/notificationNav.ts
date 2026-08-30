/** 通知行点击的统一行为(通知中心与顶栏 Popover 同一条路径,两处不许再分叉):
 *  标已读 → 结构化 target_id 精确深链(实例详情/工单对话)→ 按类型落列表页。 */

import type { NotificationOut } from "@superdl/api-client";
import { useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { useMarkNotificationRead } from "../api/mutations";

/** 实例类通知类型(target_id = 实例 uuid → 实例详情) */
const INSTANCE_TYPES = new Set(["instance", "preempted", "subscription", "gpu_fault"]);

/** 通知类型 → 兜底跳转目标(无 target_id 时落列表页) */
function fallbackOf(type: string): "/billing" | "/instances" | "/support" | null {
  switch (type) {
    case "balance_warn":
    case "arrears":
    case "invoice":
    case "refund":
    case "subscription":
      return "/billing";
    case "instance":
    case "preempted":
    case "gpu_fault":
      return "/instances";
    case "ticket":
      return "/support";
    default:
      return null;
  }
}

/** 返回通知行点击处理器;afterNavigate 供调用方在跳转后收尾(如关闭 Popover)。 */
export function useNotificationOpen(afterNavigate?: () => void) {
  const navigate = useNavigate();
  const markRead = useMarkNotificationRead();
  return useCallback(
    (n: NotificationOut) => {
      if (!n.read_at) markRead.mutate(n.id);
      // 结构化深链优先:实例类 → /instances/$uuid;工单 → /support/$ticketId
      if (n.target_id && INSTANCE_TYPES.has(n.type)) {
        void navigate({ to: "/instances/$uuid", params: { uuid: n.target_id } });
        afterNavigate?.();
        return;
      }
      if (n.target_id && n.type === "ticket") {
        void navigate({ to: "/support/$ticketId", params: { ticketId: n.target_id } });
        afterNavigate?.();
        return;
      }
      const target = fallbackOf(n.type);
      if (target) {
        void navigate({ to: target });
        afterNavigate?.();
      }
    },
    [navigate, markRead, afterNavigate],
  );
}
