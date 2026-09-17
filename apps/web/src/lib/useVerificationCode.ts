import type { VerificationCodeRequest } from "@superdl/api-client";
import { App } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useRequestHandleCode, useSendVerificationCode } from "../api/mutations";
import { requestCaptchaToken } from "./captcha";

const RESEND_SECONDS = 60;

export type CodePurpose = VerificationCodeRequest["purpose"] | "bind_handle";

/** Send a verification code to an email or E.164 phone handle with a 60 s resend countdown.
 *  Public purposes go through CAPTCHA (when enabled) and `/auth/verification-code`; `bind_handle`
 *  uses the signed-in `/me/handles/code` route. */
export function useVerificationCode(purpose: CodePurpose, sentText: string) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [countdown, setCountdown] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (countdown <= 0 && timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, [countdown]);
  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
    },
    [],
  );
  const started = () => {
    message.success(sentText);
    if (timer.current) clearInterval(timer.current);
    setCountdown(RESEND_SECONDS);
    timer.current = setInterval(() => setCountdown((c) => (c > 0 ? c - 1 : 0)), 1000);
  };
  const sendPublic = useSendVerificationCode({ onSuccess: started });
  const sendBind = useRequestHandleCode({ onSuccess: started });
  const send = (handle: string): void => {
    void (async () => {
      if (purpose === "bind_handle") {
        sendBind.mutate({ handle });
        return;
      }
      try {
        const captcha_token = await requestCaptchaToken();
        sendPublic.mutate({ handle, purpose, captcha_token });
      } catch {
        message.error(t("login.captchaUnavailable"));
      }
    })();
  };
  return { send, countdown, sending: sendPublic.isPending || sendBind.isPending };
}

export type VerificationCodeSender = ReturnType<typeof useVerificationCode>;
