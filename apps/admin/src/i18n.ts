/** 管理端 i18n 运行时:admin ns + 共享 shared/errors 目录。
 *  初始化壳(探测顺序/fallback)在 @superdl/ui 的 initAppI18n,这里只挂 admin ns 语言包。 */
import { initAppI18n, SUPPORTED_LANGS } from "@superdl/ui";

import adminEn from "./locales/en-US/admin.json";
import adminZh from "./locales/zh-CN/admin.json";

const i18n = initAppI18n({ appNs: "admin", appResources: { "zh-CN": adminZh, "en-US": adminEn } });

export { SUPPORTED_LANGS };
export type { AppLang } from "@superdl/ui";

export default i18n;
