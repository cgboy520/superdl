/** Admin i18n: admin namespace + shared shared/errors; the shell is initAppI18n from @superdl/ui. */
import { initAppI18n, SUPPORTED_LANGS } from "@superdl/ui";

import adminEn from "./locales/en-US/admin.json";
import adminZh from "./locales/zh-CN/admin.json";

const i18n = initAppI18n({ appNs: "admin", appResources: { "zh-CN": adminZh, "en-US": adminEn } });

export { SUPPORTED_LANGS };
export type { AppLang } from "@superdl/ui";

export default i18n;
