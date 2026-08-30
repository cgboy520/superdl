/** 手输原因类字段的前端口径(入审计):trim 后 2~200 字;上限判 trim 后长度,防止「原因 + 尾空格」绕过。 */

export const REASON_MAX_LEN = 200;

export function isValidReason(v: string | null | undefined): boolean {
  if (v == null) return false;
  const trimmed = v.trim();
  return trimmed.length >= 2 && trimmed.length <= REASON_MAX_LEN;
}
