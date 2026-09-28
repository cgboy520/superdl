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

interface AuthSession {
  sessionId: string | null;
  accessToken: string | null;
}

interface ClientConfig {
  baseUrl: string;
  getToken: () => string | null;
  /** Atomic snapshot; login/logout changes the generation, renewal alone preserves it. */
  getSession: (() => AuthSession) | null;
  onUnauthorized: ((expectedSessionId: string | null) => void | Promise<void>) | null;
  /** Runs under withAuthSessionLock: commit conditionally, without acquiring the lock again. */
  refreshToken: ((expectedSessionId: string | null) => Promise<boolean>) | null;
  /** Current UI language sent as Accept-Language (server-rendered texts such as verification codes). */
  getLocale: () => string | null;
}

const config: ClientConfig = {
  baseUrl: "",
  getToken: () => null,
  getSession: null,
  onUnauthorized: null,
  refreshToken: null,
  getLocale: () => null,
};

/** All auth persistence writers share this cross-tab mutex, including explicit login/logout. */
const REFRESH_LOCK = "superdl:token-refresh";

/** Not reentrant. Refresh callbacks already hold this lock; only their synchronous commit runs there. */
export function withAuthSessionLock<T>(action: () => T | Promise<T>): Promise<T> {
  return navigator.locks.request(REFRESH_LOCK, action);
}

/** Re-check the request's session inside the lock, including after waiting for another tab. */
async function refreshOnce(staleToken: string | null, currentSession: () => AuthSession): Promise<boolean> {
  return withAuthSessionLock(async () => {
    const current = currentSession();
    if (!current.accessToken) return false;
    if (current.accessToken !== staleToken) return true;
    const refreshed = (await config.refreshToken?.(current.sessionId)) ?? false;
    currentSession();
    return refreshed;
  });
}

function readSession(): AuthSession {
  return config.getSession?.() ?? { sessionId: null, accessToken: config.getToken() };
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
  const session = readSession();
  const explicitAuth = toHeaders(options.headers).has("Authorization");
  const currentSession = (): AuthSession => {
    options.signal?.throwIfAborted();
    const current = readSession();
    if (
      current.sessionId !== session.sessionId ||
      (current.accessToken === null) !== (session.accessToken === null) ||
      (!config.getSession && current.accessToken !== session.accessToken)
    ) {
      throw new DOMException("Authentication session changed", "AbortError");
    }
    return current;
  };
  let usedToken: string | null = null;
  const doFetch = (): Promise<Response> => {
    const headers = toHeaders(options.headers);
    const token = currentSession().accessToken;
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
      currentSession();
      if (e instanceof TypeError) throw networkError();
      throw e;
    }
  };

  let response = await guardedFetch();
  currentSession();

  if (response.status === 401 && config.refreshToken && !explicitAuth && !url.includes("/auth/")) {
    if (await refreshOnce(usedToken, currentSession)) {
      response = await guardedFetch();
    }
    currentSession();
  }

  const text = await response.text();
  currentSession();
  if (response.status === 401 && !explicitAuth) {
    // refreshOnce has released the lock; unauthorized logout may safely acquire it.
    await config.onUnauthorized?.(session.sessionId);
  }
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
