import { describe, expect, it } from "vitest";

import { defaultDiskName, isValidMountPath, suggestMountPath } from "./disks";

describe("isValidMountPath", () => {
  it("accepts the API's three families", () => {
    for (const p of ["/data", "/data2", "/datasets", "/workspace", "/mnt/models", "/mnt/a.b-c_d"]) {
      expect(isValidMountPath(p), p).toBe(true);
    }
  });
  it("refuses system paths, the tenant root, nesting and traversal", () => {
    for (const p of ["/", "/root", "/etc", "/mnt", "/mnt/org", "/mnt/a/b", "/data/x", "/mnt/..", "data", "/mnt/.x"]) {
      expect(isValidMountPath(p), p).toBe(false);
    }
  });
});

describe("suggestMountPath", () => {
  it("prefers /data, then the first free numbered path", () => {
    expect(suggestMountPath([])).toBe("/data");
    expect(suggestMountPath(["/data", "/data2"])).toBe("/data3");
    expect(suggestMountPath(["/mnt/x"])).toBe("/data");
  });
});

describe("defaultDiskName", () => {
  it("stamps the local minute", () => {
    expect(defaultDiskName(new Date(2026, 10, 9, 8, 5))).toBe("data-20261109-0805");
  });
});
