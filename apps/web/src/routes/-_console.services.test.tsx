/**
 * 三条服务路由的 URL 状态往返:非法值必须剥离回默认,否则会渲染出无选中态的 Tabs / 筛选,
 * 或把旧链接的 ?tab=service 当成合法 Tab。
 */
import { describe, expect, it } from "vitest";

import { servicesValidateSearch } from "./_console.services";
import { serviceDetailValidateSearch } from "./_console.services_.$slug";
import { deployValidateSearch } from "./_console.services_.new";

describe("servicesValidateSearch", () => {
  it("status 只认派生态白名单且不含 released;q 空白剥离", () => {
    expect(servicesValidateSearch({ status: "running", q: " qwen " })).toEqual({
      status: "running",
      q: " qwen ",
    });
    expect(servicesValidateSearch({ status: "released" })).toEqual({});
    expect(servicesValidateSearch({ status: "creating", q: "   " })).toEqual({});
  });
});

describe("serviceDetailValidateSearch", () => {
  it("七个合法 tab 保留;旧链接的 ?tab=service 与未知值回默认", () => {
    for (const tab of ["overview", "keys", "metrics", "logs", "events", "bills", "settings"]) {
      expect(serviceDetailValidateSearch({ tab })).toEqual({ tab });
    }
    expect(serviceDetailValidateSearch({ tab: "service" })).toEqual({});
    expect(serviceDetailValidateSearch({ tab: "xyz" })).toEqual({});
  });
});

describe("deployValidateSearch", () => {
  it("sku_id 正整数、gpus 1~8、period 压过 market=spot、count 只在包周期下有效", () => {
    expect(
      deployValidateSearch({ sku_id: "3", gpus: "2", period: "month", market: "spot", count: "6" }),
    ).toEqual({ sku_id: 3, gpus: 2, period: "month", count: 6 });
    expect(deployValidateSearch({ sku_id: "0", gpus: "9", market: "spot", count: "6" })).toEqual({
      market: "spot",
    });
    expect(deployValidateSearch({ period: "quarter" })).toEqual({});
  });
});
