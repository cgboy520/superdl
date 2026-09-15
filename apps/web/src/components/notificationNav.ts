/** 通知行点击的统一行为(通知中心与顶栏 Popover 同一条路径):标已读 → 结构化 target_id 深链(实例详情/工单对话)→ 按类型落列表页。 */

import type { NotificationOut } from "@superdl/api-client";
import { useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { useMarkNotificationRead } from "../api/mutations";

/** 实例类通知类型(target_id = 实例 uuid → 实例详情) */
const INSTANCE_TYPES = new Set(["instance", "preempted", "subscription", "gpu_fault"]);
/** 在线服务类通知(target_id = 服务 slug → 服务详情) */
const SERVICE_TYPES = new Set(["service"]);

/** 通知类型 → 兜底跳转目标(无 target_id 时落列表页) */
function fallbackOf(type: string): "/billing" | "/instances" | "/services" | "/support" | null {
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
    case "service":
      return "/services";
    default:
      return null;
  }
}

/** 通知行点击处理器;afterNavigate 供调用方在跳转后收尾。 */
export function useNotificationOpen(afterNavigate?: () => void) {
  const navigate = useNavigate();
  const markRead = useMarkNotificationRead();
  return useCallback(
    (n: NotificationOut) => {
      if (!n.read_at) markRead.mutate(n.id);
      if (n.target_id && INSTANCE_TYPES.has(n.type)) {
        void navigate({ to: "/instances/$uuid", params: { uuid: n.target_id } });
        afterNavigate?.();
        return;
      }
      if (n.target_id && SERVICE_TYPES.has(n.type)) {
        void navigate({ to: "/services/$slug", params: { slug: n.target_id } });
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
