/** skus 路由 validateSearch:型号 / 档位 / 在售 / 名称白名单与往返。挂了 = SKU 筛选不再落 URL,或非法值污染查询参数。 */
import { describe, expect, it } from "vitest";

import { skusValidateSearch } from "./skus";

describe("skus validateSearch", () => {
  it("合法值原样保留:型号任意字符串,档位取 skuTierMap 键,sale 只认 on/off", () => {
    expect(skusValidateSearch({ model: "RTX4090", tier: "shared_hami", sale: "off", q: "4090" })).toEqual({
      model: "RTX4090",
      tier: "shared_hami",
      sale: "off",
      q: "4090",
    });
    expect(skusValidateSearch({ sale: "on" })).toEqual({ sale: "on" });
  });

  it("非法与空值剥离:档位白名单外、sale 其它值、空串与未知参数一律不落", () => {
    expect(skusValidateSearch({ tier: "bogus", sale: "all", model: "  ", q: "", foo: "bar" })).toEqual({});
  });
});
