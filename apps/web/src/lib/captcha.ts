/** 人机校验(阿里云验证码 2.0)前端接入。行为按 /auth/captcha-config 的 enabled 决定:
 *  关闭则不加载 SDK、发码不带 token;开启则动态加载 AliyunCaptcha.js(仅一次),
 *  经隐藏触发按钮拉起弹窗,回调 captchaVerifyParam 取得的一次性 token 随业务请求提交。
 *  token 一次性且 20 分钟内有效(阿里云约束),每次发码都必须重新拉起验证,不许复用。 */
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

/** 弹窗验证最长等待:超时 reject 防 Promise 悬挂(用户关弹窗/网络中断无回调)。 */
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
      // 失败必须清缓存:留着 rejected promise 会让本次会话后续调用全部瞬败
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
    trigger.style.display = "none"; // 隐藏触发钮:由发送验证码按钮程序化点击
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
    mode: "popup", // 弹窗形态:不占表单布局,验证通过即关
    element: `#${BOX_ID}`,
    button: `#${TRIGGER_ID}`,
    captchaVerifyParam: (param: string) => {
      // 阿里云 success 回调:一次性 token 到手,交给等待中的业务调用
      if (pendingTimer) {
        clearTimeout(pendingTimer);
        pendingTimer = null;
      }
      pendingResolve?.(param);
      pendingResolve = null;
    },
    onBizResultCallback: () => {},
    getInstance: () => {},
    slideStyle: { width: 360, height: 40 },
    language: document.documentElement.lang.startsWith("en") ? "en" : "cn",
    region: "cn",
  });
  sdkInitialized = true;
}

/** 获取一次人机校验 token;开关关闭时返回 undefined(发码不带 token)。
 *  SDK 加载失败/不可用/验证超时则 reject(调用方提示刷新重试)。 */
export async function requestCaptchaToken(): Promise<string | undefined> {
  const cfg = await getConfig();
  if (!cfg.enabled) return undefined;
  await initAliyun(cfg);
  return new Promise<string>((resolve, reject) => {
    pendingResolve = resolve;
    pendingTimer = setTimeout(() => {
      // 弹窗被关闭/验证无响应:reject 让调用方走出 loading,可立即重试
      pendingResolve = null;
      pendingTimer = null;
      reject(new Error("captcha verify timeout"));
    }, CAPTCHA_TIMEOUT_MS);
    document.getElementById(TRIGGER_ID)?.click(); // 拉起验证码弹窗;关闭弹窗=放弃(用户可重试)
  });
}
