/** 幂等键派生:同一表单快照 → 同一键,失败不轮换;成功后再开新单由调用方把序号/nonce 放进 parts。 */
export function idemKeyOf(scope: string, parts: ReadonlyArray<string | number | null | undefined>): string {
  const s = `${scope}:${parts.map((p) => p ?? "").join("|")}`;
  let h = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  for (let i = 0; i < s.length; i++) {
    h ^= BigInt(s.charCodeAt(i));
    h = (h * prime) & 0xffffffffffffffffn;
  }
  return `${scope}-${h.toString(16).padStart(16, "0")}`;
}
