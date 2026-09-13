/** 状态表每个 labelKey/hintKey 在 zh/en shared.json 同时存在且非空;shared/errors 目录 zh/en 齐平。 */
import { describe, expect, it } from "vitest";

import errorsEn from "../locales/en-US/errors.json";
import enUS from "../locales/en-US/shared.json";
import errorsZh from "../locales/zh-CN/errors.json";
import zhCN from "../locales/zh-CN/shared.json";
import { assertLocaleParity } from "./localeParity";
import * as status from "./status";
import { ALL_STATUS_MAPS } from "./status";

/** 按 "a.b.c" 路径取目录值;缺失或非字符串返回 undefined。 */
function lookup(catalog: Record<string, unknown>, dotted: string): string | undefined {
  let cur: unknown = catalog;
  for (const part of dotted.split(".")) {
    if (!cur || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === "string" ? cur : undefined;
}

const usedKeys: string[] = [];
for (const map of ALL_STATUS_MAPS) {
  for (const meta of Object.values<Record<string, unknown>>(map)) {
    for (const field of ["labelKey", "hintKey"]) {
      const v = meta[field];
      if (typeof v === "string") usedKeys.push(v);
    }
  }
}

describe("packages/ui shared locale", () => {
  it("status.ts 每张 *Map 都登记在 ALL_STATUS_MAPS(否则新表可以不带文案上线)", () => {
    const registered = new Set<unknown>(ALL_STATUS_MAPS);
    for (const [name, v] of Object.entries(status)) {
      if (!name.endsWith("Map") || typeof v !== "object") continue;
      expect(registered.has(v), `${name} 未登记`).toBe(true);
    }
  });

  it("每个 labelKey/hintKey 在 zh 与 en 均存在且非空", () => {
    for (const key of usedKeys) {
      const bare = key.slice("shared:".length);
      expect(lookup(zhCN, bare)?.trim(), `zh 缺 ${bare}`).toBeTruthy();
      expect(lookup(enUS, bare)?.trim(), `en 缺 ${bare}`).toBeTruthy();
    }
  });

  it("shared.json zh/en 齐平(键集、非空、占位符)", () => {
    // lang.zh「中文」是语言固有名称
    assertLocaleParity(zhCN, enUS, { allowCjkInEn: ["lang.zh"] });
  });
});

describe("errors namespace(后端 MESSAGES 生成链)", () => {
  it("errors.json zh/en 齐平(键集、非空、占位符)", () => {
    assertLocaleParity(errorsZh, errorsEn);
  });
});
