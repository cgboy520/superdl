/**
 * orval 自定义 fetch mutator:统一 baseUrl / Authorization / 错误体解析。
 * 应用启动时调用 configureApiClient() 注入 token 来源与 401 处理。
 */

export interface ApiError {
  code: string;
  message: string;
  detail?: unknown;
  status: number;
}

interface ClientConfig {
  baseUrl: string;
  getToken: () => string | null;
  onUnauthorized: (() => void) | null;
  /** 401 时尝试静默续期(返回是否成功);未配置则维持旧行为直接 onUnauthorized。 */
  refreshToken: (() => Promise<boolean>) | null;
}

const config: ClientConfig = {
  baseUrl: "",
  getToken: () => null,
  onUnauthorized: null,
  refreshToken: null,
};

/** 并发 401 共享同一次续期(single-flight),避免刷新风暴与重放误判。 */
let refreshInFlight: Promise<boolean> | null = null;

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
  const doFetch = (): Promise<Response> => {
    const headers = new Headers(options.headers);
    const token = config.getToken();
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
    refreshInFlight ??= config.refreshToken().finally(() => {
      refreshInFlight = null;
    });
    if (await refreshInFlight) {
      response = await doFetch();
    }
  }

  if (response.status === 401) {
    config.onUnauthorized?.();
  }

  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const err = (body ?? {}) as Partial<ApiError>;
    const apiError: ApiError = {
      code: err.code ?? "HTTP_ERROR",
      message: err.message ?? `请求失败(${response.status})`,
      detail: err.detail,
      status: response.status,
    };
    throw apiError;
  }
  return body as T;
};

export default customFetch;
