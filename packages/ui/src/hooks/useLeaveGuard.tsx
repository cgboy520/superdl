/** 脏表单离开防护核心(不依赖路由库):路由阻断器由各端注入(TanStack useBlocker),刷新与关标签由 beforeunload 兜底;
 *  提交成功或「取消」已确认后调 bypass() 放行;抽屉 / 弹窗自身关闭用 confirmLeave(then)。 */

import { Modal } from "antd";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface LeaveBlocker {
  status: "blocked" | "idle";
  proceed: () => void;
  reset: () => void;
}

export function useLeaveGuardCore(
  dirty: boolean,
  blocker: LeaveBlocker,
): { bypass: () => void; modal: ReactNode; confirmLeave: (then: () => void) => void; bypassed: () => boolean } {
  const { t } = useTranslation("shared");
  const bypassRef = useRef(false);
  const [pendingClose, setPendingClose] = useState<(() => void) | null>(null);
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  const bypass = useCallback(() => {
    bypassRef.current = true;
  }, []);
  const bypassed = useCallback(() => bypassRef.current, []);
  const confirmLeave = useCallback(
    (then: () => void) => {
      if (!dirty || bypassRef.current) {
        then();
        return;
      }
      setPendingClose(() => then);
    },
    [dirty],
  );
  const open = blocker.status === "blocked" || pendingClose !== null;
  const modal = (
    <Modal
      open={open}
      title={t("leave.title")}
      okText={t("leave.ok")}
      cancelText={t("leave.cancel")}
      okButtonProps={{ danger: true }}
      onOk={() => {
        if (pendingClose) {
          const fn = pendingClose;
          setPendingClose(null);
          fn();
        } else {
          blocker.proceed();
        }
      }}
      onCancel={() => {
        if (pendingClose) setPendingClose(null);
        else blocker.reset();
      }}
    >
      {t("leave.body")}
    </Modal>
  );
  return { bypass, modal, confirmLeave, bypassed };
}
