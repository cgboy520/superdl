/** locale 守护:五张状态表的每个 labelKey/hintKey 必须在 zh/en shared.json 同时存在且非空。 */
import { describe, expect, it } from "vitest";

import errorsEn from "../locales/en-US/errors.json";
import enUS from "../locales/en-US/shared.json";
import errorsZh from "../locales/zh-CN/errors.json";
import zhCN from "../locales/zh-CN/shared.json";
import {
  announcementStatusMap,
  diskStatusMap,
  imageCacheStatusMap,
  instanceStatusMap,
  invoiceStatusMap,
  nodeEnrollStatusMap,
  payoutChannelMap,
  refundStatusMap,
  skuTierMap,
  ticketCategoryMap,
  ticketStatusMap,
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
for (const map of [instanceStatusMap, skuTierMap, imageCacheStatusMap, nodeEnrollStatusMap, diskStatusMap, refundStatusMap, payoutChannelMap, invoiceStatusMap, ticketStatusMap, ticketCategoryMap, announcementStatusMap]) {
  for (const meta of Object.values<Record<string, unknown>>(map)) {
    for (const field of ["labelKey", "hintKey"]) {
      const v = meta[field];
      if (typeof v === "string") usedKeys.push(v);
    }
  }
}

describe("packages/ui shared locale", () => {
  it("每个 labelKey/hintKey 在 zh 与 en 均存在且非空", () => {
    for (const key of usedKeys) {
      const bare = key.slice("shared:".length);
      expect(zhFlat.get(bare)?.trim(), `zh 缺 ${bare}`).toBeTruthy();
      expect(enFlat.get(bare)?.trim(), `en 缺 ${bare}`).toBeTruthy();
    }
  });

  it("zh/en 键集相等(复数后缀归一后;shared.json 不留孤儿键)", () => {
    const base = (k: string) => k.replace(/_(one|other|zero|two|few|many)$/, "");
    const zhBase = new Set([...zhFlat.keys()].map(base));
    const enBase = new Set([...enFlat.keys()].map(base));
    expect([...enBase].sort()).toEqual([...zhBase].sort());
  });
});

describe("errors namespace(后端 MESSAGES 生成链)", () => {
  const zhE = flatten(errorsZh);
  const enE = flatten(errorsEn);
  const ph = (v: string) => [...v.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]!).sort();

  it("zh/en 键集相等", () => {
    expect([...enE.keys()].sort()).toEqual([...zhE.keys()].sort());
  });

  it("值非空且占位符逐键一致", () => {
    for (const [key, zhV] of zhE) {
      const enV = enE.get(key);
      expect(zhV.trim(), `zh 空值 ${key}`).not.toBe("");
      expect(enV?.trim(), `en 空值 ${key}`).toBeTruthy();
      if (enV) expect(ph(enV), key).toEqual(ph(zhV));
    }
  });
});
