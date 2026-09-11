/** locale 目录守护:zh/en 键集相等、值非空、{{占位符}} 逐键一致(断言函数在 packages/ui)。 */
import { assertLocaleParity } from "@superdl/ui";
import { it } from "vitest";

import enUS from "./en-US/web.json";
import zhCN from "./zh-CN/web.json";

it("web 目录 zh/en 齐平(键集、非空、占位符)", () => {
  assertLocaleParity(zhCN, enUS);
});
