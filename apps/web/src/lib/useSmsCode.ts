import type { SmsCodeRequest } from "@superdl/api-client";
import { App } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSendSmsCode } from "../api/mutations";
import { requestCaptchaToken } from "./captcha";

const RESEND_SECONDS = 60;

/** 短信发码:人机校验、发送请求与 60 秒重发倒计时。 */
export function useSmsCode(purpose: SmsCodeRequest["purpose"], sentText: string) {
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
  const sendCode = useSendSmsCode({
    onSuccess: () => {
      message.success(sentText);
      if (timer.current) clearInterval(timer.current);
      setCountdown(RESEND_SECONDS);
      timer.current = setInterval(() => setCountdown((c) => (c > 0 ? c - 1 : 0)), 1000);
    },
  });
  /** 人机校验先行(安全策略开启时):拿到一次性 token 才发码;关闭时直接发码;SDK 不可用提示刷新。 */
  const send = (phone: string): void => {
    void (async () => {
      try {
        const captcha_token = await requestCaptchaToken();
        sendCode.mutate({ phone, purpose, captcha_token });
      } catch {
        message.error(t("login.captchaUnavailable"));
      }
    })();
  };
  return { send, countdown, sending: sendCode.isPending };
}
