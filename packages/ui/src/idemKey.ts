/** Idempotency key derivation: the same form snapshot → the same key, never rotated on failure; for a fresh order after success the caller puts a sequence / nonce into parts. */
export function idemKeyOf(scope: string, parts: readonly (string | number | null | undefined)[]): string {
  const s = `${scope}:${parts.map((p) => p ?? "").join("|")}`;
  let h = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  for (let i = 0; i < s.length; i++) {
    h ^= BigInt(s.charCodeAt(i));
    h = (h * prime) & 0xffffffffffffffffn;
  }
  return `${scope}-${h.toString(16).padStart(16, "0")}`;
}
