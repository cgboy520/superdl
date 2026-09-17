/** Admin console zh/en copy: key sets, non-empty values and placeholder parity. */
import { assertLocaleParity } from "@superdl/ui/localeParity";
import { it } from "vitest";

import enUS from "./en-US/admin.json";
import zhCN from "./zh-CN/admin.json";

it("admin namespace zh/en parity (key set, non-empty values, placeholders)", () => {
  assertLocaleParity(zhCN, enUS, {
    allowCjkInEn: ["platform.field.icp_number", "platform.field.police_record_number"],
  });
});
