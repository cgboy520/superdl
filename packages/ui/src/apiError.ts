/**
 * API 错误 → 展示文案:有 message_key 则查 errors ns(带 params,缺键自动回落服务端中文),
 * 无 key(旧错误体/网络层兜底)直接用 message。
 * t 需接受任意字符串键(server key 天然动态)——应用侧经 useApiErrorText() 单点收窄。
 */

export interface ApiErrorLike {
  code?: string;
  message?: string;
  message_key?: string | null;
  params?: Record<string, unknown> | null;
}

export type LooseT = (key: string, opts?: Record<string, unknown>) => string;

export function apiErrorText(t: LooseT, err: unknown, fallback: string): string {
  const e = (err ?? undefined) as ApiErrorLike | undefined;
  if (e?.message_key) {
    return t(e.message_key, {
      ns: "errors",
      defaultValue: e.message ?? fallback,
      ...(e.params ?? {}),
    });
  }
  return e?.message ?? fallback;
}
