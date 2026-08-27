/** 跨 spec 文件共用的小工具。 */

/**
 * 生成一个本轮唯一的测试手机号(139 + 8 位)。
 *
 * **必须带随机位,不能只用 Date.now()**:playwright 默认按文件并行,两个 spec 同一毫秒
 * 起跑就会拿到同一个号,于是一个被发码限流挡成 429、另一个注册 400,失败现象是
 * 「停在 /login 页」,跟被测功能毫无关系,而且只在多文件并行时才复现。
 * 用毫秒后 5 位 + 3 位随机:既保留时间前缀(便于在库里按时间找出测试用户),
 * 又把同毫秒碰撞概率压到千分之一以下。
 */
export function uniquePhone(): string {
  const ms = String(Date.now()).slice(-5);
  const rand = String(Math.floor(Math.random() * 1000)).padStart(3, "0");
  return `139${ms}${rand}`;
}

/** 构造合法且指纹唯一的 ed25519 公钥(与后端 blob 校验一致)。 */
export function genEd25519Key(): string {
  const type = "ssh-ed25519";
  const typeBytes = new TextEncoder().encode(type);
  const keyBytes = crypto.getRandomValues(new Uint8Array(32));
  const blob = new Uint8Array(4 + typeBytes.length + 4 + 32);
  const dv = new DataView(blob.buffer);
  dv.setUint32(0, typeBytes.length);
  blob.set(typeBytes, 4);
  dv.setUint32(4 + typeBytes.length, 32);
  blob.set(keyBytes, 8 + typeBytes.length);
  let bin = "";
  for (const b of blob) bin += String.fromCharCode(b);
  const b64 = btoa(bin);
  return `${type} ${b64} e2e@smoke`;
}
