/** Cloudflare Turnstile: explicit render of one execute-on-demand widget; the challenge UI shows
 *  only when Cloudflare needs an interaction. Tokens are single-use, so the widget is reset after
 *  every resolution. Requests are serialized (one challenge at a time, each owning its own
 *  callbacks and timer) and the widget is re-rendered when the site key changes. */
import type { CaptchaConfigOut } from "@superdl/api-client";

import { CAPTCHA_TIMEOUT_MS } from "./index";

const SDK_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const BOX_ID = "superdl-turnstile-box";

interface TurnstileApi {
  render: (container: HTMLElement | string, options: Record<string, unknown>) => string;
  execute: (widgetId: string) => void;
  reset: (widgetId: string) => void;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

interface Pending {
  resolve: (token: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

let sdkReady: Promise<void> | null = null;
let widget: { id: string; siteKey: string } | null = null;
let pending: Pending | null = null;
let queue: Promise<unknown> = Promise.resolve();

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

/** Finish the current request (if any) and return it so the caller can settle it. */
function takePending(): Pending | null {
  const current = pending;
  pending = null;
  if (current) clearTimeout(current.timer);
  if (widget) window.turnstile?.reset(widget.id);
  return current;
}

function box(): HTMLElement {
  let el = document.getElementById(BOX_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = BOX_ID;
    el.style.position = "fixed";
    el.style.right = "16px";
    el.style.bottom = "16px";
    el.style.zIndex = "2000";
    document.body.appendChild(el);
  }
  return el;
}

async function ensureWidget(cfg: CaptchaConfigOut): Promise<string> {
  const siteKey = cfg.site_key ?? "";
  if (widget?.siteKey === siteKey) return widget.id;
  await loadSdk();
  if (!window.turnstile) throw new Error("captcha sdk unavailable");
  if (widget) window.turnstile.remove(widget.id);
  const id = window.turnstile.render(box(), {
    sitekey: siteKey,
    execution: "execute",
    appearance: "interaction-only",
    theme: "auto",
    language: document.documentElement.lang.startsWith("zh") ? "zh-cn" : "en",
    callback: (token: string) => takePending()?.resolve(token),
    "error-callback": () => takePending()?.reject(new Error("captcha verify failed")),
    "expired-callback": () => takePending()?.reject(new Error("captcha verify expired")),
  });
  widget = { id, siteKey };
  return id;
}

async function acquire(cfg: CaptchaConfigOut): Promise<string> {
  const id = await ensureWidget(cfg);
  return new Promise<string>((resolve, reject) => {
    pending = {
      resolve,
      reject,
      timer: setTimeout(() => takePending()?.reject(new Error("captcha verify timeout")), CAPTCHA_TIMEOUT_MS),
    };
    window.turnstile?.execute(id);
  });
}

export function requestTurnstileToken(cfg: CaptchaConfigOut): Promise<string> {
  const run = queue.then(
    () => acquire(cfg),
    () => acquire(cfg),
  );
  queue = run.catch(() => undefined);
  return run;
}
