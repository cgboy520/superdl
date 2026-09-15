/** 管理端 zh/en 文案键集、非空值与占位符一致性测试。 */
import { assertLocaleParity } from "@superdl/ui/localeParity";
import { it } from "vitest";

import enUS from "./en-US/admin.json";
import zhCN from "./zh-CN/admin.json";

it("admin 目录 zh/en 齐平(键集、非空、占位符)", () => {
  assertLocaleParity(zhCN, enUS, {
    allowCjkInEn: ["platform.field.icp_number", "platform.field.police_record_number"],
  });
});
