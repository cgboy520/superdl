/** i18n 运行时初始化(main.tsx 顶部副作用引入,先于首次 render)。
 *  初始化壳(探测顺序/fallback/共享 shared+errors 目录)在 @superdl/ui 的 initAppI18n,这里只挂 web ns 语言包。 */
import { initAppI18n, SUPPORTED_LANGS } from "@superdl/ui";

import enUS from "./locales/en-US/web.json";
import zhCN from "./locales/zh-CN/web.json";

const i18n = initAppI18n({ appNs: "web", appResources: { "zh-CN": zhCN, "en-US": enUS } });

export { SUPPORTED_LANGS };
export type { AppLang } from "@superdl/ui";

export default i18n;
