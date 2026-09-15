/** 阿里云验证码接入:按配置加载 SDK,通过弹窗回调获取验证 token。 */
import { captchaConfigApiV1AuthCaptchaConfigGet } from "@superdl/api-client";
import type { CaptchaConfigOut } from "@superdl/api-client";

const SDK_URL = "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js";
const TRIGGER_ID = "superdl-aliyun-captcha-trigger";
const BOX_ID = "superdl-aliyun-captcha-box";

declare global {
  interface Window {
    initAliyunCaptcha?: (options: Record<string, unknown>) => void;
  }
}

let configPromise: Promise<CaptchaConfigOut> | null = null;
let sdkReady: Promise<void> | null = null;
let sdkInitialized = false;
let pendingResolve: ((token: string) => void) | null = null;
let pendingTimer: ReturnType<typeof setTimeout> | null = null;

/** 弹窗验证最长等待,超时 reject。 */
const CAPTCHA_TIMEOUT_MS = 120_000;

function getConfig(): Promise<CaptchaConfigOut> {
  configPromise ??= captchaConfigApiV1AuthCaptchaConfigGet();
  return configPromise;
}

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

function ensureContainers(): void {
  if (!document.getElementById(TRIGGER_ID)) {
    const trigger = document.createElement("button");
    trigger.id = TRIGGER_ID;
    trigger.type = "button";
    trigger.style.display = "none";
    document.body.appendChild(trigger);
  }
  if (!document.getElementById(BOX_ID)) {
    const box = document.createElement("div");
    box.id = BOX_ID;
    document.body.appendChild(box);
  }
}

async function initAliyun(cfg: CaptchaConfigOut): Promise<void> {
  if (sdkInitialized) return;
  await loadSdk();
  if (typeof window.initAliyunCaptcha !== "function") {
    throw new Error("captcha sdk unavailable");
  }
  ensureContainers();
  window.initAliyunCaptcha({
    SceneId: cfg.scene_id,
    prefix: cfg.prefix,
    mode: "popup",
    element: `#${BOX_ID}`,
    button: `#${TRIGGER_ID}`,
    captchaVerifyParam: (param: string) => {
      if (pendingTimer) {
        clearTimeout(pendingTimer);
        pendingTimer = null;
      }
      pendingResolve?.(param);
      pendingResolve = null;
    },
    onBizResultCallback: () => undefined,
    getInstance: () => undefined,
    slideStyle: { width: 360, height: 40 },
    language: document.documentElement.lang.startsWith("en") ? "en" : "cn",
    region: "cn",
  });
  sdkInitialized = true;
}

/** 获取一次人机校验 token;开关关闭返回 undefined。SDK 加载失败 / 不可用 / 验证超时则 reject。 */
export async function requestCaptchaToken(): Promise<string | undefined> {
  const cfg = await getConfig();
  if (!cfg.enabled) return undefined;
  await initAliyun(cfg);
  return new Promise<string>((resolve, reject) => {
    pendingResolve = resolve;
    pendingTimer = setTimeout(() => {
      pendingResolve = null;
      pendingTimer = null;
      reject(new Error("captcha verify timeout"));
    }, CAPTCHA_TIMEOUT_MS);
    document.getElementById(TRIGGER_ID)?.click();
  });
}
