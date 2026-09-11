/** TOTP(RFC 6238,SHA1/30s/6 位)最小实现,与后端 pyotp 默认参数一致;管理端 e2e 用它现算动态码。 */
import { createHmac } from "node:crypto";

const B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(input: string): Buffer {
  const clean = input.replace(/=+$/, "").replace(/\s/g, "").toUpperCase();
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const c of clean) {
    const idx = B32_ALPHABET.indexOf(c);
    if (idx < 0) throw new Error(`非法 base32 字符: ${c}`);
    value = ((value << 5) | idx) >>> 0;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

/** 指定时刻的 TOTP 码;默认当前时间。 */
export function totp(secret: string, atMs: number = Date.now()): string {
  const key = base32Decode(secret);
  const counter = BigInt(Math.floor(atMs / 1000 / 30));
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(counter);
  const digest = createHmac("sha1", key).update(msg).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary =
    ((digest[offset] & 0x7f) << 24) |
    (digest[offset + 1] << 16) |
    (digest[offset + 2] << 8) |
    digest[offset + 3];
  return String(binary % 1_000_000).padStart(6, "0");
}

/** 同一密钥上次提交过的时间步(服务端要求 timestep 单调)。 */
const lastStepBySecret = new Map<string, number>();

/** 填入当前 TOTP:距时间窗边界 <3s 先等下一步;同一密钥同一 30s 窗内已提交过则等到下一窗。 */
export async function fillTotp(
  input: {
    fill: (v: string) => Promise<unknown>;
    page: () => { waitForTimeout: (ms: number) => Promise<unknown> };
  },
  secret: string,
): Promise<void> {
  let now = Date.now();
  let remain = 30_000 - (now % 30_000);
  if (remain < 3_000 || lastStepBySecret.get(secret) === Math.floor(now / 30_000)) {
    await input.page().waitForTimeout(remain + 100);
    now = Date.now();
    remain = 30_000 - (now % 30_000);
  }
  lastStepBySecret.set(secret, Math.floor(now / 30_000));
  await input.fill(totp(secret, now));
}
