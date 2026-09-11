/** 手输原因字段口径:trim 后 2~200 字。 */

export const REASON_MAX_LEN = 200;

export function isValidReason(v: string | null | undefined): boolean {
  if (v == null) return false;
  const trimmed = v.trim();
  return trimmed.length >= 2 && trimmed.length <= REASON_MAX_LEN;
}
