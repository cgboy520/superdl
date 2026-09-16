/** CAPTCHA token acquisition: reads /auth/captcha-config once and delegates to the configured
 *  provider's loader (Aliyun Captcha 2.0 or Cloudflare Turnstile). */
import { captchaConfigApiV1AuthCaptchaConfigGet } from "@superdl/api-client";
import type { CaptchaConfigOut } from "@superdl/api-client";

import { requestAliyunToken } from "./aliyun";
import { requestTurnstileToken } from "./turnstile";

let configPromise: Promise<CaptchaConfigOut> | null = null;

/** Popup verification waits at most this long before rejecting. */
export const CAPTCHA_TIMEOUT_MS = 120_000;

function getConfig(): Promise<CaptchaConfigOut> {
  configPromise ??= captchaConfigApiV1AuthCaptchaConfigGet();
  return configPromise;
}

/** One-time CAPTCHA token; undefined when the switch is off. Rejects when the SDK fails to load,
 *  is unavailable, or the challenge times out. */
export async function requestCaptchaToken(): Promise<string | undefined> {
  const cfg = await getConfig();
  if (!cfg.enabled) return undefined;
  if (cfg.provider === "turnstile") return requestTurnstileToken(cfg);
  return requestAliyunToken(cfg);
}
