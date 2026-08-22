/**
 * orval 自定义 fetch mutator:统一 baseUrl / Authorization / 错误体解析。
 * 应用启动时调用 configureApiClient() 注入 token 来源与 401 处理。
 */

export interface ApiError {
  code: string;
  message: string;
  detail?: unknown;
  status: number;
  /** 多语言目录键(errors ns);缺失时前端回落 message(服务端渲染的中文) */
  message_key?: string | null;
  params?: Record<string, unknown> | null;
}

interface ClientConfig {
  baseUrl: string;
  getToken: () => string | null;
  onUnauthorized: (() => void) | null;
  /** 401 时尝试静默续期(返回是否成功);未配置则直接 onUnauthorized。 */
  refreshToken: (() => Promise<boolean>) | null;
}

const config: ClientConfig = {
  baseUrl: "",
  getToken: () => null,
  onUnauthorized: null,
  refreshToken: null,
};

/** 同标签页内并发 401 共享同一次续期(无 Web Locks 时的兜底 single-flight)。 */
let refreshInFlight: Promise<boolean> | null = null;

/** 跨标签页续期互斥锁名。后端 refresh 是一次性消费,重放即被判定泄露并撤销全部会话。 */
const REFRESH_LOCK = "superdl:token-refresh";

/**
 * 续期一次。staleToken 是发起该请求时用的 access token:进入临界区后 token 已变,
 * 说明别的标签页/并发请求刚续期成功,直接重放。
 * 后端 refresh 一次性消费,重放旧 token 会撤销该用户全部会话。
 */
async function refreshOnce(staleToken: string | null): Promise<boolean> {
  const run = async (): Promise<boolean> => {
    if (config.getToken() !== staleToken) return true;
    return (await config.refreshToken?.()) ?? false;
  };
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request(REFRESH_LOCK, run);
  }
  refreshInFlight ??= run().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

export function configureApiClient(opts: Partial<ClientConfig>): void {
  Object.assign(config, opts);
}

export function isApiError(e: unknown): e is ApiError {
  return typeof e === "object" && e !== null && "code" in e && "status" in e;
}

/** 直连用户端刷新接口(绕过拦截器,避免 401→refresh 递归)。失败返回 null。 */
export async function requestTokenRefresh(
  refreshToken: string,
): Promise<{ access_token: string; refresh_token: string } | null> {
  try {
    const resp = await fetch(`${config.baseUrl}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as { access_token: string; refresh_token: string };
  } catch {
    return null;
  }
}

export const customFetch = async <T>(url: string, options: RequestInit): Promise<T> => {
  let usedToken: string | null = null;
  const doFetch = (): Promise<Response> => {
    const headers = new Headers(options.headers);
    const token = config.getToken();
    usedToken = token;
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    return fetch(`${config.baseUrl}${url}`, { ...options, headers });
  };

  let response = await doFetch();

  // 401 先静默续期重放一次(登录/刷新接口本身除外),失败才交给 onUnauthorized
  if (response.status === 401 && config.refreshToken && !url.includes("/auth/")) {
    if (await refreshOnce(usedToken)) {
      response = await doFetch();
    }
  }

  if (response.status === 401) {
    config.onUnauthorized?.();
  }

  const text = await response.text();
  // 网关 502/504 返回 HTML 而非错误体,直接 JSON.parse 会抛 SyntaxError
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      if (response.ok) throw new Error("响应不是合法 JSON");
      body = null;
    }
  }

  if (!response.ok) {
    const err = (body ?? {}) as Partial<ApiError>;
    const apiError: ApiError = {
      code: err.code ?? "HTTP_ERROR",
      message: err.message ?? `请求失败(${response.status})`,
      message_key: err.message_key ?? null,
      params: err.params ?? null,
      detail: err.detail,
      status: response.status,
    };
    throw apiError;
  }
  return body as T;
};

export default customFetch;
