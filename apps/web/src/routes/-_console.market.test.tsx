/** 市场页 validateSearch:10 个筛选/选中参数的 URL 往返。挂了说明:刷新/分享/返回丢筛选,或默认值把 URL 弄脏。 */
import { describe, expect, it } from "vitest";

import { marketValidateSearch } from "./_console.market";

describe("market validateSearch", () => {
  it("10 参数全量往返:合法值全部保留(数字串归一为数字)", () => {
    const input = {
      kind: "cpu",
      mode: "month",
      model: "H100",
      tier: "dedicated",
      vram: "80",
      qty: "4",
      vcpu: "16",
      mem: "128",
      sku: "42",
      count: "3",
    };
    expect(marketValidateSearch(input)).toEqual({
      kind: "cpu",
      mode: "month",
      model: "H100",
      tier: "dedicated",
      vram: 80,
      qty: 4,
      vcpu: 16,
      mem: 128,
      sku: 42,
      count: 3,
    });
  });

  it("默认值剥离:kind=gpu / mode=on_demand / qty=1 / count=1 / 空档与 0 值不进 URL", () => {
    expect(
      marketValidateSearch({
        kind: "gpu",
        mode: "on_demand",
        model: "",
        tier: "",
        vram: 0,
        qty: 1,
        vcpu: 0,
        mem: 0,
        count: 1,
      }),
    ).toEqual({});
  });

  it("非法值丢弃:未知 kind/mode/tier、负数、浮点、超上限时长、非数字一律回默认", () => {
    expect(
      marketValidateSearch({
        kind: "tpu",
        mode: "hourly",
        tier: "cpu",
        vram: -1,
        qty: 2.5,
        vcpu: "abc",
        mem: null,
        sku: 0,
        count: 99,
      }),
    ).toEqual({});
  });

  it("竞价与周期档位识别:mode=spot 保留,展示档位(shared_mig)保留", () => {
    expect(marketValidateSearch({ mode: "spot", tier: "shared_mig" })).toEqual({
      mode: "spot",
      tier: "shared_mig",
    });
  });

  it("URL 往返序列化:validate 输出再 validate 幂等(控件受控于 search 不漂移)", () => {
    const once = marketValidateSearch({
      kind: "cpu",
      mode: "week",
      vcpu: "8",
      mem: "64",
      sku: "7",
    });
    expect(marketValidateSearch(once as Record<string, unknown>)).toEqual(once);
  });
});
