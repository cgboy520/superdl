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
  /** Silent renewal attempt for a 401 outside /auth/ paths (returns success); onUnauthorized is called when unconfigured or still 401 after the retry. */
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

/** Cross-tab renewal mutex name. */
const REFRESH_LOCK = "superdl:token-refresh";

/** Renew serially inside Web Locks; returns success at once when the current token differs from the request's token. */
async function refreshOnce(staleToken: string | null): Promise<boolean> {
  return navigator.locks.request(REFRESH_LOCK, async () => {
    if (config.getToken() !== staleToken) return true;
    return (await config.refreshToken?.()) ?? false;
  });
}

export function configureApiClient(opts: Partial<ClientConfig>): void {
  Object.assign(config, opts);
}

/** Request options whose header record allows null or undefined values. */
export interface ApiRequestOptions extends Omit<RequestInit, "headers"> {
  headers?: HeadersInit | Record<string, string | null | undefined>;
}

/** Drop null and undefined values from the record when building Headers. */
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

/** POST the refresh endpoint directly, null on failure. */
async function postRefresh(path: string, init: RequestInit): Promise<{ access_token: string } | null> {
  try {
    const resp = await fetch(`${config.baseUrl}${path}`, { method: "POST", ...init });
    if (!resp.ok) return null;
    return (await resp.json()) as { access_token: string };
  } catch {
    return null;
  }
}

/** User-side renewal with the same-origin cookie and X-Requested-With, no body. */
export function requestTokenRefresh(): Promise<{ access_token: string } | null> {
  return postRefresh("/api/v1/auth/refresh", {
    credentials: "same-origin",
    headers: { "X-Requested-With": "fetch" },
  });
}

/** Admin renewal with the current access token. */
export function requestAdminTokenRefresh(accessToken: string): Promise<{ access_token: string } | null> {
  return postRefresh("/api/admin/v1/auth/refresh", {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken }),
  });
}

/** Network-layer failures (offline / DNS / connection refused) become ApiError; fetch's TypeError text is not exposed. */
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
