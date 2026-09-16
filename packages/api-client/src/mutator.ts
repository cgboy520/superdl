/**
 * orval custom fetch mutator: one place for baseUrl, Authorization, Accept-Language and error-body
 * parsing. Apps call configureApiClient() at startup to inject the token source and 401 handling.
 */

export interface ApiError {
  code: string;
  message: string;
  detail?: unknown;
  status: number;
  /** Catalog key (errors ns); when absent the client falls back to `message` (English from the server) */
  message_key?: string | null;
  params?: Record<string, unknown> | null;
}

interface ClientConfig {
  baseUrl: string;
  getToken: () => string | null;
  onUnauthorized: (() => void) | null;
  /** 非 /auth/ 路径的 401 尝试静默续期(返回是否成功);未配置或重试后仍为 401 时调用 onUnauthorized。 */
  refreshToken: (() => Promise<boolean>) | null;
  /** Current UI language sent as Accept-Language (server-rendered texts such as verification codes). */
  getLocale: () => string | null;
}

const config: ClientConfig = {
  baseUrl: "",
  getToken: () => null,
  onUnauthorized: null,
  refreshToken: null,
  getLocale: () => null,
};

/** 跨标签页续期互斥锁名。 */
const REFRESH_LOCK = "superdl:token-refresh";

/** 在 Web Locks 内串行续期;当前 token 不同于请求时的 token 时直接返回成功。 */
async function refreshOnce(staleToken: string | null): Promise<boolean> {
  return navigator.locks.request(REFRESH_LOCK, async () => {
    if (config.getToken() !== staleToken) return true;
    return (await config.refreshToken?.()) ?? false;
  });
}

export function configureApiClient(opts: Partial<ClientConfig>): void {
  Object.assign(config, opts);
}

/** 请求选项,支持值为 null 或 undefined 的 header 记录。 */
export interface ApiRequestOptions extends Omit<RequestInit, "headers"> {
  headers?: HeadersInit | Record<string, string | null | undefined>;
}

/** 构造 Headers 时剔除记录中的 null 与 undefined 值。 */
function toHeaders(init: ApiRequestOptions["headers"]): Headers {
  if (!init) return new Headers();
  if (init instanceof Headers || Array.isArray(init)) return new Headers(init);
  const h = new Headers();
  for (const [k, v] of Object.entries(init)) {
    if (v != null) h.set(k, v);
  }
  return h;
}

export function isApiError(e: unknown): e is ApiError {
  return typeof e === "object" && e !== null && "code" in e && "status" in e;
}

/** 直接 POST 刷新接口,失败返回 null。 */
async function postRefresh(path: string, init: RequestInit): Promise<{ access_token: string } | null> {
  try {
    const resp = await fetch(`${config.baseUrl}${path}`, { method: "POST", ...init });
    if (!resp.ok) return null;
    return (await resp.json()) as { access_token: string };
  } catch {
    return null;
  }
}

/** 使用同源 Cookie 与 X-Requested-With 请求用户端续期,不带请求体。 */
export function requestTokenRefresh(): Promise<{ access_token: string } | null> {
  return postRefresh("/api/v1/auth/refresh", {
    credentials: "same-origin",
    headers: { "X-Requested-With": "fetch" },
  });
}

/** 使用当前 access token 请求管理端续期。 */
export function requestAdminTokenRefresh(accessToken: string): Promise<{ access_token: string } | null> {
  return postRefresh("/api/admin/v1/auth/refresh", {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken }),
  });
}

/** 网络层失败(断网/DNS/连接拒绝)统一成 ApiError,不把 fetch 的 TypeError 原文透出。 */
function networkError(): ApiError {
  return {
    code: "NETWORK_ERROR",
    message: "Network connection failed — check your connection and try again",
    message_key: "common.networkError",
    params: null,
    status: 0,
  };
}

export const customFetch = async <T>(url: string, options: ApiRequestOptions): Promise<T> => {
  let usedToken: string | null = null;
  const doFetch = (): Promise<Response> => {
    const headers = toHeaders(options.headers);
    const token = config.getToken();
    usedToken = token;
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const locale = config.getLocale();
    if (locale && !headers.has("Accept-Language")) {
      headers.set("Accept-Language", locale);
    }
    return fetch(`${config.baseUrl}${url}`, { ...options, headers });
  };

  const guardedFetch = async (): Promise<Response> => {
    try {
      return await doFetch();
    } catch (e) {
      if (e instanceof TypeError) throw networkError();
      throw e;
    }
  };

  let response = await guardedFetch();

  if (response.status === 401 && config.refreshToken && !url.includes("/auth/")) {
    if (await refreshOnce(usedToken)) {
      response = await guardedFetch();
    }
  }

  if (response.status === 401) {
    config.onUnauthorized?.();
  }

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      if (response.ok) return text as T;
    }
  }

  if (!response.ok) {
    const err = (body ?? {}) as Partial<ApiError>;
    const serverMessage = typeof err.message === "string" && err.message !== "" ? err.message : undefined;
    const apiError: ApiError = {
      code: err.code ?? "HTTP_ERROR",
      message: serverMessage ?? `Request failed (${response.status})`,
      message_key: err.message_key ?? (serverMessage ? null : "common.httpError"),
      params: err.params ?? (serverMessage ? null : { status: response.status }),
      detail: err.detail,
      status: response.status,
    };
    throw apiError;
  }
  return body as T;
};
