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
}

const config: ClientConfig = {
  baseUrl: "",
  getToken: () => null,
  onUnauthorized: null,
};

export function configureApiClient(opts: Partial<ClientConfig>): void {
  Object.assign(config, opts);
}

export function isApiError(e: unknown): e is ApiError {
  return typeof e === "object" && e !== null && "code" in e && "status" in e;
}

export const customFetch = async <T>(url: string, options: RequestInit): Promise<T> => {
  const headers = new Headers(options.headers);
  const token = config.getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${config.baseUrl}${url}`, { ...options, headers });

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
