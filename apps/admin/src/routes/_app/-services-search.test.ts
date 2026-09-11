/** services 路由 validateSearch:q / user_id / released 白名单与往返。挂了 = 检索词不再落 URL,或非法值污染查询参数。 */
import { describe, expect, it } from "vitest";

import { servicesValidateSearch } from "./services";

describe("services validateSearch", () => {
  it("合法值原样保留,released 只认 1", () => {
    expect(servicesValidateSearch({ q: "svc-ab", user_id: "7", released: "1" })).toEqual({
      q: "svc-ab",
      user_id: 7,
      released: "1",
    });
  });

  it("空 q、非正整数 user_id、其它 released 值与未知参数一律剥离", () => {
    expect(servicesValidateSearch({ q: "  ", user_id: "0", released: "yes", foo: "bar" })).toEqual({});
    expect(servicesValidateSearch({ user_id: "abc" })).toEqual({});
  });
});
