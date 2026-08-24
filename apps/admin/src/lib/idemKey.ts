/**
 * 幂等键派生:同一表单快照 → 同一键(双击/重试/网络丢响应安全重放);
 * 快照变更 → 新键(真正的新单据)。FNV-1a 64-bit:确定性、零依赖,
 * 碰撞域对本量级(单据数远小于 2^32)可忽略。失败不轮换——
 * 轮换会让「其实已创建成功但响应丢失」的重试再建一单。
 */
export function idemKeyOf(
  scope: string,
  parts: ReadonlyArray<string | number | null | undefined>,
): string {
  const s = `${scope}:${parts.map((p) => p ?? "").join("|")}`;
  let h = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  for (let i = 0; i < s.length; i++) {
    h ^= BigInt(s.charCodeAt(i));
    h = (h * prime) & 0xffffffffffffffffn;
  }
  return `${scope}-${h.toString(16).padStart(16, "0")}`;
}
