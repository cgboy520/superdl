/** Typed reason field rule: 2–200 characters after trim. */

export const REASON_MAX_LEN = 200;

export function isValidReason(v: string | null | undefined): boolean {
  if (v == null) return false;
  const trimmed = v.trim();
  return trimmed.length >= 2 && trimmed.length <= REASON_MAX_LEN;
}
