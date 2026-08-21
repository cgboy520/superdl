/** locale 目录守护:zh/en 键集相等、值非空、{{占位符}} 逐键一致 —— 缺失键的确定性闸门。 */
import { describe, expect, it } from "vitest";

import enUS from "./en-US/web.json";
import zhCN from "./zh-CN/web.json";

function flatten(obj: Record<string, unknown>, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out.set(key, v);
    else if (v && typeof v === "object") {
      for (const [ck, cv] of flatten(v as Record<string, unknown>, key)) out.set(ck, cv);
    }
  }
  return out;
}

function placeholders(value: string): string[] {
  return [...value.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]!).sort();
}

const CATALOGS: Array<[string, Record<string, unknown>, Record<string, unknown>]> = [
  ["web", zhCN, enUS],
];

describe.each(CATALOGS)("locales/%s", (_ns, zh, en) => {
  const zhFlat = flatten(zh);
  const enFlat = flatten(en);

  it("zh/en 键集相等", () => {
    expect([...enFlat.keys()].sort()).toEqual([...zhFlat.keys()].sort());
  });

  it("值非空", () => {
    for (const [key, value] of [...zhFlat, ...enFlat]) {
      expect(value.trim(), key).not.toBe("");
    }
  });

  it("占位符逐键一致", () => {
    for (const [key, zhValue] of zhFlat) {
      const enValue = enFlat.get(key);
      if (enValue == null) continue; // 键集断言已覆盖
      expect(placeholders(enValue), key).toEqual(placeholders(zhValue));
    }
  });
});
