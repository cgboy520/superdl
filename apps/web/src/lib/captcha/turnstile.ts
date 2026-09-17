/** Cloudflare Turnstile: explicit render of one execute-on-demand widget; the challenge UI shows
 *  only when Cloudflare needs an interaction. Tokens are single-use, so the widget is reset after
 *  every resolution. */
import type { CaptchaConfigOut } from "@superdl/api-client";

import { CAPTCHA_TIMEOUT_MS } from "./index";

const SDK_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const BOX_ID = "superdl-turnstile-box";

interface TurnstileApi {
  render: (container: HTMLElement | string, options: Record<string, unknown>) => string;
  execute: (widgetId: string) => void;
  reset: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let sdkReady: Promise<void> | null = null;
let widgetId: string | null = null;
let pendingResolve: ((token: string) => void) | null = null;
let pendingReject: ((err: Error) => void) | null = null;
let pendingTimer: ReturnType<typeof setTimeout> | null = null;

function loadSdk(): Promise<void> {
  sdkReady ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SDK_URL;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      sdkReady = null;
      reject(new Error("captcha sdk load failed"));
    };
    document.head.appendChild(script);
  });
  return sdkReady;
}

function settle(): void {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
  pendingResolve = null;
  pendingReject = null;
  if (widgetId && window.turnstile) window.turnstile.reset(widgetId);
}

async function ensureWidget(cfg: CaptchaConfigOut): Promise<string> {
  if (widgetId) return widgetId;
  await loadSdk();
  if (!window.turnstile) throw new Error("captcha sdk unavailable");
  let box = document.getElementById(BOX_ID);
  if (!box) {
    box = document.createElement("div");
    box.id = BOX_ID;
    box.style.position = "fixed";
    box.style.right = "16px";
    box.style.bottom = "16px";
    box.style.zIndex = "2000";
    document.body.appendChild(box);
  }
  widgetId = window.turnstile.render(box, {
    sitekey: cfg.site_key,
    execution: "execute",
    appearance: "interaction-only",
    theme: "auto",
    language: document.documentElement.lang.startsWith("zh") ? "zh-cn" : "en",
    callback: (token: string) => {
      const resolve = pendingResolve;
      settle();
      resolve?.(token);
    },
    "error-callback": () => {
      const reject = pendingReject;
      settle();
      reject?.(new Error("captcha verify failed"));
    },
    "expired-callback": () => {
      const reject = pendingReject;
      settle();
      reject?.(new Error("captcha verify expired"));
    },
  });
  return widgetId;
}

export async function requestTurnstileToken(cfg: CaptchaConfigOut): Promise<string> {
  const id = await ensureWidget(cfg);
  return new Promise<string>((resolve, reject) => {
    pendingResolve = resolve;
    pendingReject = reject;
    pendingTimer = setTimeout(() => {
      settle();
      reject(new Error("captcha verify timeout"));
    }, CAPTCHA_TIMEOUT_MS);
    window.turnstile?.execute(id);
  });
}
