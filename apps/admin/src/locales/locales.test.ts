/** locale 目录守护:zh/en 键集相等、值非空、{{占位符}} 逐键一致(断言函数在 packages/ui,preservePatterns 保护的动态键只有它守)。 */
import { assertLocaleParity } from "@superdl/ui/localeParity";
import { it } from "vitest";

import enUS from "./en-US/admin.json";
import zhCN from "./zh-CN/admin.json";

it("admin 目录 zh/en 齐平(键集、非空、占位符)", () => {
  // 备案制度的中文原名在 en 侧保留(docs/copy-style-guide.md:中国渠道运营域专名不译)
  assertLocaleParity(zhCN, enUS, {
    allowCjkInEn: ["platform.field.icp_number", "platform.field.police_record_number"],
  });
});
