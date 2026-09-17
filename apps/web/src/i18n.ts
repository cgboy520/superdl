/** i18n runtime initialisation (imported at the top of main.tsx). The shell is initAppI18n from @superdl/ui; this only registers the web namespace bundles. */
import { initAppI18n, SUPPORTED_LANGS } from "@superdl/ui";

import enUS from "./locales/en-US/web.json";
import zhCN from "./locales/zh-CN/web.json";

const i18n = initAppI18n({ appNs: "web", appResources: { "zh-CN": zhCN, "en-US": enUS } });

export { SUPPORTED_LANGS };
export type { AppLang } from "@superdl/ui";

export default i18n;
