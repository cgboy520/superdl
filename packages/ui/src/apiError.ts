/** API 错误 → 展示文案:有 message_key 查 errors ns(缺键回落服务端 message),无 key 用 message。应用侧经 useApiErrorText()。 */

export interface ApiErrorLike {
  code?: string;
  message?: string;
  message_key?: string | null;
  params?: Record<string, unknown> | null;
}

export type LooseT = (key: string, opts?: Record<string, unknown>) => string;

export function apiErrorText(t: LooseT, err: unknown, fallback: string): string {
  // unknown → 结构读取;可选链兜住 null/非对象
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
