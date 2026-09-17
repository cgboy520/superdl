import { describe, expect, it } from "vitest";

import errorsEn from "../locales/en-US/errors.json";
import enUS from "../locales/en-US/shared.json";
import errorsZh from "../locales/zh-CN/errors.json";
import zhCN from "../locales/zh-CN/shared.json";
import { assertLocaleParity } from "./localeParity";
import * as status from "./status";
import { ALL_STATUS_MAPS } from "./status";

/** Read a catalog value by "a.b.c" path; undefined when missing or not a string. */
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
  it("every *Map in status.ts is registered in ALL_STATUS_MAPS (otherwise a new table could ship without copy)", () => {
    const registered = new Set<unknown>(ALL_STATUS_MAPS);
    for (const [name, v] of Object.entries(status)) {
      if (!name.endsWith("Map") || typeof v !== "object") continue;
      expect(registered.has(v), `${name} not registered`).toBe(true);
    }
  });

  it("every labelKey/hintKey exists and is non-empty in zh and en", () => {
    for (const key of usedKeys) {
      const bare = key.slice("shared:".length);
      expect(lookup(zhCN, bare)?.trim(), `zh missing ${bare}`).toBeTruthy();
      expect(lookup(enUS, bare)?.trim(), `en missing ${bare}`).toBeTruthy();
    }
  });

  it("shared.json zh/en parity (key set, non-empty, placeholders)", () => {
    assertLocaleParity(zhCN, enUS, { allowCjkInEn: ["lang.zh"] });
  });
});

describe("errors namespace (generated from the backend MESSAGES)", () => {
  it("errors.json zh/en parity (key set, non-empty, placeholders)", () => {
    assertLocaleParity(errorsZh, errorsEn);
  });
});
