/** locale 守护:五张状态表的每个 labelKey/hintKey 必须在 zh/en shared.json 同时存在且非空。 */
import { describe, expect, it } from "vitest";

import enUS from "../locales/en-US/shared.json";
import zhCN from "../locales/zh-CN/shared.json";
import {
  diskStatusMap,
  imageCacheStatusMap,
  instanceStatusMap,
  nodeEnrollStatusMap,
  skuTierMap,
} from "./status";

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

const zhFlat = flatten(zhCN);
const enFlat = flatten(enUS);

const usedKeys: string[] = [];
for (const map of [instanceStatusMap, skuTierMap, imageCacheStatusMap, nodeEnrollStatusMap, diskStatusMap]) {
  for (const meta of Object.values<Record<string, unknown>>(map)) {
    for (const field of ["labelKey", "hintKey"]) {
      const v = meta[field];
      if (typeof v === "string") usedKeys.push(v);
    }
  }
}

describe("packages/ui shared locale", () => {
  it("状态表 key 全部带 shared: 前缀", () => {
    for (const key of usedKeys) expect(key.startsWith("shared:"), key).toBe(true);
  });

  it("每个 labelKey/hintKey 在 zh 与 en 均存在且非空", () => {
    for (const key of usedKeys) {
      const bare = key.slice("shared:".length);
      expect(zhFlat.get(bare)?.trim(), `zh 缺 ${bare}`).toBeTruthy();
      expect(enFlat.get(bare)?.trim(), `en 缺 ${bare}`).toBeTruthy();
    }
  });

  it("zh/en 键集相等(shared.json 不留孤儿键)", () => {
    expect([...enFlat.keys()].sort()).toEqual([...zhFlat.keys()].sort());
  });
});
