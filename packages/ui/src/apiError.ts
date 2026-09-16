/** API error → display copy: with message_key look up the errors ns (falling back to the server message on a missing key), without a key use message. Apps go through useApiErrorText(). */

export interface ApiErrorLike {
  code?: string;
  message?: string;
  message_key?: string | null;
  params?: Record<string, unknown> | null;
}

export type LooseT = (key: string, opts?: Record<string, unknown>) => string;

export function apiErrorText(t: LooseT, err: unknown, fallback: string): string {
  const e = err as ApiErrorLike | undefined;
  if (e?.message_key) {
    return t(e.message_key, {
      ns: "errors",
      defaultValue: e.message ?? fallback,
      ...(e.params ?? {}),
    });
  }
  return e?.message ?? fallback;
}
