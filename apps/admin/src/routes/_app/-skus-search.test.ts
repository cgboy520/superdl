/** SKU 路由型号、档位、在售与名称筛选的 URL 往返测试。 */
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
