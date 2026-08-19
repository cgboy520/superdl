import { describe, expect, it } from "vitest";

import { toCsv } from "./csv";

describe("toCsv", () => {
  it("BOM 头 + CRLF 行分隔", () => {
    const out = toCsv(["a", "b"], [["1", "2"]]);
    expect(out.startsWith("\ufeff")).toBe(true);
    expect(out).toBe("\ufeffa,b\r\n1,2");
  });
  it("金额字符串原样保留(不丢精度不变形)", () => {
    const out = toCsv(["金额"], [["0.0350"], ["-12.30"]]);
    expect(out).toContain("0.0350");
    expect(out).toContain("-12.30");
  });
  it("逗号/引号/换行转义", () => {
    const out = toCsv(["x"], [['a,"b"\nc']]);
    expect(out).toBe('\ufeffx\r\n"a,""b""\nc"');
  });
  it("空值输出空串", () => {
    expect(toCsv(["x", "y"], [[null, undefined]])).toBe("\ufeffx,y\r\n,");
  });
});
