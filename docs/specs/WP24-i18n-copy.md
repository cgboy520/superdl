# WP24 · 文案体系与全面 i18n

决策:
1. 语言集 zh-CN(基准)+ en-US,两端全量。
2. 工具链 react-i18next + i18next + i18next-cli(key 提取 / TS 类型生成 / 硬编码 lint / locale 同步);复数用 i18next 原生 plural(`_one/_other` 后缀,Intl.PluralRules),不引 ICU 库。
3. 后端错误走 message_key + params:同一 ErrorCode 对应多条文案(VALIDATION_ERROR、INSTANCE_INVALID_TRANSITION 等一码多文),「前端按 code 查表」会令文案坍缩,不采用;`core/messages.py` 集中目录为单一事实源(不做每调用点 key+中文双份——必漂移),响应体 `message` 保留中文兜底。错误体由 exception handler 拼装、不在 openapi schema 内(消费方为手写 `mutator.ts`),message_key/params 不产生契约 diff。
4. packages/ui 保持零运行时依赖:状态表导出 labelKey(键内嵌 `shared:` 前缀,`as const satisfies` 保字面量类型),t 由调用组件显式持有(强制订阅,杜绝陈旧文案)。
5. 明确后置:短信多语言(阿里云逐语言报备签名/模板,唯一外部阻塞)、站内信随语言(需改存 type+params 的模型变更,历史通知保持原语言)、legal 双语(法务流程,工程不产文案)、openapi 描述英文化、每路由动态 title。admin `platform.tsx` 渠道操作指引等中国渠道运营域文案决策不译(CJK 闸门豁免)。

## 机制

- 基建:两端各有 `src/i18n.ts`(LanguageDetector:localStorage `superdl.lang` → navigator;fallbackLng zh-CN)+ `lib/locale.ts`(antd ConfigProvider locale / dayjs locale / html lang / document.title 四联动的唯一出口)+ `LangSwitcher`(web 顶栏与登录页、admin 顶栏);web ConfigProvider 收敛在 `AppProviders`(主树/错误边界/404 共用)。
- namespace:`web` / `admin` / `shared`(`packages/ui/locales/`:状态表 + format 量词,手维护 + ui 单测守护)/ `errors`(zh 由后端 MESSAGES 经 `scripts/export_error_messages.py` 生成、CI no-diff 锁;en 手译,键集与 `{{param}}` 占位符 parity 由 ui 单测锁)。资源静态 import 全量打包,不做懒加载。
- format 函数带 locale/t 尾参;en 用缩写单位规避复数,真复数点 formatDaysLeft 走 `_one/_other`;金额数字管线纯字符串运算不过 Number;en 货币符号 `CN¥`(裸 ¥ 在英文语境读作日元);formatDateTime 两语言统一 `YYYY-MM-DD HH:mm`(控制台表格定宽可排序);format 测试双语参数化真实渲染(ui devDependencies 含 i18next 仅测试用)。
- 后端:`AppError(code, message=None, *, key=None, params=None, ...)` 兼容签名;`render_message` 缺键回落 key + 告警不 500;422/500 兜底 `common.validation`/`common.internal`;响应体 `{code, message, message_key, params, detail}`;测试断 code 不断中文 message。
- 前端消费:`ApiError` 含 `message_key/params`;`packages/ui/src/apiError.ts` 纯函数 `apiErrorText(t, err, fallback)`(有 key → 查 errors ns 带 params,缺键回落服务端中文);两 app 各一 `useApiErrorText()`(动态 server key 的唯一 cast 收敛点);web `mutations.ts` 为统一错误出口。
- 文案规范见 `docs/copy-style-guide.md`;约束类文案以后端 message_key 为单一事实源,前端禁用 tooltip 与错误弹窗同键。

## 工具链与 CI 闸门

- 每 app 一份 `i18next.config.ts`:extract(sort、removeUnusedKeys)、types(产物 `src/types/i18next.d.ts` 入库,CI `--ci` 校验无 diff;input 同时吃 app 与 packages/ui 的 zh JSON)、lint(硬编码/插值 mismatch/字符串拼接)。JSON 为唯一值载体,代码不写 defaultValue(改文案直接改 JSON 可独立 review);数组文案逐条字面量键(不用 returnObjects,保全静态)。
- 每 app `src/locales/locales.test.ts`(vitest):zh/en 键集相等、值非空、`{{}}` 占位符逐键一致 = 缺失键确定性闸门;packages/ui `locales.test.ts`:状态表 labelKey/hintKey 在 zh/en shared.json 均存在,errors.json 键集与占位符 parity。
- CJK 残留闸门:web 由 `i18next-cli lint`(AST 级)守护,admin 由 `scripts/check-cjk.sh`(剥注释 grep,platform.tsx 等运营域豁免);禁词由 `scripts/check-copy-banned.sh` 强制。
- 接线:app scripts `"i18n"` 聚合上述检查;turbo 任务 + Taskfile + CI 前端 job 跑 `pnpm i18n`;api job 含 error-messages 导出 no-diff。
- e2e:`playwright.config.ts` 钉 `use.locale: "zh-CN"`(CI chromium 默认英文环境会令中文定位器失效);关键路径元素带 data-testid;en 冒烟 spec(localStorage 预置 en-US,断言公开页无 CJK 残留/无 `xxx.yyy` 键泄漏)。

## 数据变更

无 DB 迁移;错误响应体含 `message_key`/`params`(增量字段,旧客户端忽略即兼容),openapi.json 无 diff。

## 验收用例

1. `pnpm i18n` 全绿:extract 无新增键、types 无 diff、lint 0 硬编码;locales.test zh/en parity 全过;CJK/禁词闸门 0 命中(白名单外)。
2. 切语言(web 顶栏):antd 组件文案/日期选择器/状态徽标/金额符号/时长量词/菜单/表格列/错误弹窗全部跟随;localStorage 记忆,刷新保持;html lang 同步。
3. 后端:`AppError(key=..., params=...)` 响应含 message_key/params 且 message 为渲染后中文;`AppError(code, "中文")` 直传行为不变;422/500 带兜底 key;缺键 render 回落 key 不 500。
4. 前端错误展示:带 message_key 的 4xx → en 环境显示英文、zh 显示中文;无 key 的错误体回落 message。
5. e2e:zh 主链路 smoke 全绿;en 冒烟 spec(/login /market 无 CJK、无键泄漏、切换器可用)。

## 人工事项

- 阿里云英文短信签名/模板报备(周级周期);大陆签名仅中文,国际短信需 SendMessageToGlobe 另评估。
- legal terms/privacy 英文版法务起草审定(工程预留 per-locale 整页资源位)。
