/** Dirty-form leave guard (web): the router blocker is TanStack useBlocker; the dialog, beforeunload and confirmLeave live in @superdl/ui useLeaveGuardCore. */

import { useLeaveGuardCore } from "@superdl/ui";
import { useBlocker } from "@tanstack/react-router";
import { useCallback, useRef, type ReactNode } from "react";

const noop = () => undefined;

export function useLeaveGuard(dirty: boolean): {
  bypass: () => void;
  modal: ReactNode;
  confirmLeave: (then: () => void) => void;
} {
  const bypassRef = useRef(false);
  const blocker = useBlocker({ shouldBlockFn: () => dirty && !bypassRef.current, withResolver: true });
  const core = useLeaveGuardCore(
    dirty,
    blocker.status === "blocked"
      ? { status: "blocked", proceed: blocker.proceed, reset: blocker.reset }
      : { status: "idle", proceed: noop, reset: noop },
  );
  const coreBypass = core.bypass;
  const bypass = useCallback(() => {
    bypassRef.current = true;
    coreBypass();
  }, [coreBypass]);
  return { bypass, modal: core.modal, confirmLeave: core.confirmLeave };
}
