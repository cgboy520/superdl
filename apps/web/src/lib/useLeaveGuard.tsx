/** 脏表单离开防护:路由跳走(侧栏 / 前进后退)由 useBlocker 拦,刷新与关标签由 beforeunload 兜底;
 *  提交成功或「取消」已确认后调 bypass() 放行,避免同一动作二次确认。 */

import { useBlocker } from "@tanstack/react-router";
import { Modal } from "antd";
import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function useLeaveGuard(dirty: boolean): { bypass: () => void; modal: ReactNode } {
  const { t } = useTranslation();
  const bypassRef = useRef(false);
  const { status, proceed, reset } = useBlocker({
    shouldBlockFn: () => dirty && !bypassRef.current,
    withResolver: true,
  });
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  const bypass = useCallback(() => {
    bypassRef.current = true;
  }, []);
  const modal = (
    <Modal
      open={status === "blocked"}
      title={t("create.discardConfirmTitle")}
      okText={t("create.discardConfirmOk")}
      cancelText={t("create.discardConfirmCancel")}
      okButtonProps={{ danger: true }}
      onOk={proceed}
      onCancel={reset}
    >
      {t("create.discardConfirmBody")}
    </Modal>
  );
  return { bypass, modal };
}
