import { Modal } from "antd";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface LeaveBlocker {
  status: "blocked" | "idle";
  proceed: () => void;
  reset: () => void;
}

/** 脏表单离开确认:接收路由阻断器,监听 beforeunload;提供的 bypass 不绕过 beforeunload。 */
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
