/** CAPTCHA token acquisition: reads /auth/captcha-config (cached for CONFIG_TTL_MS so an online
 *  provider switch reaches open tabs without a reload) and delegates to the configured provider's
 *  loader (Aliyun Captcha 2.0 or Cloudflare Turnstile). */
import { captchaConfigApiV1AuthCaptchaConfigGet } from "@superdl/api-client";
import type { CaptchaConfigOut } from "@superdl/api-client";

import { requestAliyunToken } from "./aliyun";
import { requestTurnstileToken } from "./turnstile";

/** Popup verification waits at most this long before rejecting. */
export const CAPTCHA_TIMEOUT_MS = 120_000;
/** How long one /auth/captcha-config answer is reused. */
export const CONFIG_TTL_MS = 60_000;

let cached: { config: CaptchaConfigOut; fetchedAt: number } | null = null;
let inflight: Promise<CaptchaConfigOut> | null = null;

function getConfig(): Promise<CaptchaConfigOut> {
  if (cached && Date.now() - cached.fetchedAt < CONFIG_TTL_MS) return Promise.resolve(cached.config);
  inflight ??= captchaConfigApiV1AuthCaptchaConfigGet()
    .then((config) => {
      cached = { config, fetchedAt: Date.now() };
      return config;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Test seam: forget the cached configuration. */
export function resetCaptchaConfigCache(): void {
  cached = null;
  inflight = null;
}

/** One-time CAPTCHA token; undefined when the switch is off. Rejects when the SDK fails to load,
 *  is unavailable, or the challenge times out. */
export async function requestCaptchaToken(): Promise<string | undefined> {
  const cfg = await getConfig();
  if (!cfg.enabled) return undefined;
  if (cfg.provider === "turnstile") return requestTurnstileToken(cfg);
  return requestAliyunToken(cfg);
}
