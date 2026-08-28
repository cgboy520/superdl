/**
 * 幂等键派生:同一表单快照 → 同一键(双击/重试/网络丢响应安全重放),快照变更 → 新键。
 * 失败一律不轮换,否则「已创建成功但响应丢失」的重试会再建一单。
 * 同一表单成功后要再开新单的场景,调用方把提交序号/挂载 nonce 放进 parts。
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
