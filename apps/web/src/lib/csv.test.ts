import { describe, expect, it } from "vitest";

import { toCsv } from "./csv";

describe("toCsv", () => {
  it("BOM 头 + CRLF 行分隔", () => {
    const out = toCsv(["a", "b"], [["1", "2"]]);
    expect(out).toBe("\ufeffa,b\r\n1,2");
  });
  it("逗号/引号/换行转义", () => {
    const out = toCsv(["x"], [['a,"b"\nc']]);
    expect(out).toBe('\ufeffx\r\n"a,""b""\nc"');
  });
  it("空值输出空串", () => {
    expect(toCsv(["x", "y"], [[null, undefined]])).toBe("\ufeffx,y\r\n,");
  });
  it("公式注入防护:危险前导字符转文本,负数金额不受影响", () => {
    expect(toCsv(["x"], [["+SUM(A1)"]])).toBe("\ufeffx\r\n'+SUM(A1)");
    expect(toCsv(["x"], [["@who"]])).toBe("\ufeffx\r\n'@who");
    expect(toCsv(["x"], [["-2+3"]])).toBe("\ufeffx\r\n'-2+3");
    expect(toCsv(["x"], [["=1+1"]])).toBe("\ufeffx\r\n'=1+1");
    expect(toCsv(["金额"], [["-12.30"]])).toBe("\ufeff金额\r\n-12.30");
  });
});
