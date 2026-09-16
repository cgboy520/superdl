/** Unified behaviour of a notification row click (the notification centre and the top-bar Popover share it): mark read → structured target_id deep link (instance detail / ticket conversation) → list page by type. */

import type { NotificationOut } from "@superdl/api-client";
import { useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { useMarkNotificationRead } from "../api/mutations";

/** Instance notification types (target_id = instance uuid → instance detail) */
const INSTANCE_TYPES = new Set(["instance", "preempted", "subscription", "gpu_fault"]);
/** Online-service notifications (target_id = service slug → service detail) */
const SERVICE_TYPES = new Set(["service"]);

/** Notification type → fallback target (list page without target_id) */
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

/** Notification row click handler; afterNavigate lets the caller wrap up after navigating. */
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
