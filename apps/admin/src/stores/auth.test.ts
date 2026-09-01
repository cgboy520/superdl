/** 发票读门守护:抬头与邮箱是自然人 PII,后端 /invoices 与 /invoices/export 都只放 finance/admin。
 *  这条断言挂了,说明前端角色表与后端 require_roles 漂移了——ops/readonly 会拿到一个必 403 的
 *  发票 Tab,明文入口也跟着多长一个口子。 */
import { describe, expect, it } from "vitest";

import { canReadInvoices } from "./auth";

describe("canReadInvoices", () => {
  it("只有 finance 与 admin 能读发票(ops/readonly 一律不给)", () => {
    expect(canReadInvoices("finance")).toBe(true);
    expect(canReadInvoices("admin")).toBe(true);
    expect(canReadInvoices("ops")).toBe(false);
    expect(canReadInvoices("readonly")).toBe(false);
  });
});
