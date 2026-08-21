# WP24 · 文案体系与全面 i18n(合并施工)

方向决策经人工确认:
1. 语言集 **zh-CN(基准)+ en-US**;两端都做、用户端先交付;「去 AI 味」文案重写与 i18n 抽取**合并为同一次施工**(迁哪页,同提交重写哪页的文案)。
2. 选型 **react-i18next@17 + i18next@26 + i18next-cli@1.71**(官方工具链:key 提取 / TS 类型生成 / 硬编码 lint / locale 同步);复数用 i18next 原生 plural(`_one/_other` 后缀,Intl.PluralRules),不引 ICU 库。
3. 后端错误走 **message_key + params**:现状 28 个 ErrorCode 对应 83 条唯一文案(VALIDATION_ERROR 一码 17 文、INSTANCE_INVALID_TRANSITION 一码 6 文),「前端按 code 查表」会让文案坍缩,否决;新建 `core/messages.py` 集中目录为单一事实源(否决每调用点 key+中文双份——必漂移),响应体 `message` 保留中文兜底。已核实错误体不在 openapi schema 中(exception handler 拼装、消费方 `packages/api-client/src/mutator.ts` 为手写文件)→ **零契约 diff,无需 orval 再生成**。
4. **packages/ui 保持零运行时依赖**:状态表改导出 labelKey(键内嵌 `shared:` 前缀 + `as const satisfies` 保字面量类型),t 由调用组件显式持有(强制订阅、杜绝陈旧文案);`copy.ts`/`marketing.ts` 吸收进 web namespace 后**删除**(copy 引用全在 web,admin 为零)。
5. 明确后置(有 roadmap 背书):短信多语言(阿里云逐语言报备签名/模板,**唯一外部阻塞,人工事项建议立即启动**)、站内信随语言(需改存 type+params 的模型变更,历史通知保持原语言)、legal 双语(法务流程,工程不产文案)、openapi 描述英文化(开发者面)、每路由动态 title(并入既有 P1 项)。`platform.tsx` GROUP_INTRO 六段中国渠道操作指引**决策不译**(CJK 闸门白名单豁免)。

## 目标

- **i18n 基建**:两端各建 `src/i18n.ts`(LanguageDetector: localStorage `superdl.lang` → navigator;fallbackLng zh-CN)+ `lib/locale.ts`(antd ConfigProvider locale / dayjs locale / html lang / document.title 四联动的唯一出口)+ `LangSwitcher`(web 顶栏与登录页、admin 顶栏;en 补齐前仅 DEV 显示);web `__root.tsx` 三处重复 ConfigProvider(主树/错误边界/404)收敛为 `AppProviders`。
- **namespace 布局**:`web`(~460 键,含吸收的 copy/marketing)/ `admin`(~515 键)/ `shared`(`packages/ui/locales/`,~50 键:5 张状态表 31 条 + format 量词,手维护 + ui 单测守护)/ `errors`(~85 键,zh 由后端 MESSAGES 经 `scripts/export_error_messages.py` 生成、CI no-diff 锁;en 手译、键集与 `{{param}}` 占位符 parity 由 ui 单测锁)。资源静态 import 全量打包(双语 gzip ~25KB,不做懒加载)。
- **format 七函数**:formatMoney/formatHourlyPrice/formatDuration/formatCountdown/formatDaysLeft 加 locale/t 尾参;en 用缩写单位规避复数,唯一真复数点 formatDaysLeft 走 `_one/_other`;金额数字管线保持纯字符串运算不过 Number(铁律 #1 精神);en 货币符号 `CN¥`(裸 ¥ 在英文语境读作日元);**formatDateTime 两语言统一 `YYYY-MM-DD HH:mm` 不动**(控制台表格定宽可排序);format.test 改双语参数化真实渲染(ui devDependencies 加 i18next 仅测试用)。
- **后端机制**:`AppError(code, message=None, *, key=None, params=None, ...)` 向后兼容签名;`render_message` 缺键回落 key + 告警不 500;422/500 兜底补 `common.validation`/`common.internal`;响应体 `{code, message, message_key, params, detail}`;91 个调用点分三批 key 化(f-string 消亡为 params);后端 5 处中文 message 断言改断 code。
- **前端消费**:mutator.ts 的 `ApiError` 加 `message_key/params` 两字段;`packages/ui/src/apiError.ts` 纯函数 `apiErrorText(t, err, fallback)`(有 key → t 查 errors ns 带 params,缺键自动回落服务端中文);两 app 各一 `useApiErrorText()`(动态 server key 的唯一 cast 收敛点);web `mutations.ts` 统一错误出口与 admin ~20 处 `e.message` 逐批接入。

## 文案外科手术清单(与对应页面批次同提交执行)

- **删(零信息损失,7 处)**:nodes.tsx Grafana 部署说明占位描述、images.tsx runbook 仓库路径、settings.tsx「(免重启)」自证、storage.tsx 扩容抽屉「不支持缩容」(Slider min 已钉死)、instances.tsx「点击改名」「刷新列表」两个冗余 tooltip、login.tsx「欢迎使用 SuperDL」toast。
- **收纳**:platform.tsx GROUP_INTRO 六段(60–160 字)从常驻 Alert 改为可折叠「配置指引」(Collapse,默认收起;内容保持中文)。
- **去重(≥6 组漂移)**:「数据盘独立」4 处 3 写法收敛 1 键;antiMining 3 处引同键;低余额说明 2 页同键;约束类文案(冻结/释放/关机/扩容/删盘/SSH)以后端 message_key 为单一事实源,前端禁用 tooltip 与错误弹窗同键。
- **后端 5 例**:notify GPU 告警删「您的」「平台已介入处理」与**不存在的「代金券」承诺**;「走调账」「端口池」内部术语改用户可执行表述;余额预警短信文案压缩到首句可行动信息。
- **句式**:「标题+括号解释」×8 重写(括号内容删或入 tooltip);marketing「A · B · C」排比模具重写;「保存后即时生效…」×3 删或降为一次性提示。
- **顺带**:admin 6 处 antd6 已废弃 `Alert message=` → `title=`;landing 注释与实现不符 1 处修正。
- **规范**:新增 `docs/copy-style-guide.md`(句式规则/标点规则/禁词表:智能·强大·轻松·一键·全方位·高效·助力·赋能·极速 等);禁词表以 `scripts/check-copy-banned.sh` grep 进 CI。

## 工具链与 CI 闸门

- 每 app 一份 `i18next.config.ts`:extract(input src、output `src/locales/{{language}}/{{namespace}}.json`、sort、removeUnusedKeys)、types(产物 `src/types/i18next.d.ts` 入库,CI `--ci` 校验无 diff;input 同时吃 app 与 packages/ui 的 zh JSON)、lint(硬编码/插值 mismatch/字符串拼接)。**JSON 为唯一值载体,代码不写 defaultValue**(重写直接改 JSON 可独立 review);数组文案逐条字面量键(不用 returnObjects,保全静态)。
- 每 app `src/locales/locales.test.ts`(vitest):zh/en 键集相等、值非空、`{{}}` 占位符逐键一致 = 缺失键确定性闸门;packages/ui `locales.test.ts`:5 张状态表 labelKey/hintKey 在 zh/en shared.json 均存在,errors.json 键集与占位符 parity。
- `scripts/check-cjk.sh`:对 `apps/{web,admin}/src` 排除 locales 与白名单 grep 汉字残留;**迁完哪端对哪端激活**。
- 接线:app scripts `"i18n": "i18next-cli extract --ci && i18next-cli types --ci && i18next-cli lint"`;turbo 任务 + Taskfile check-fe + CI 前端 job 追加 `pnpm i18n`;api job 追加 error-messages 导出 no-diff。
- e2e:`playwright.config.ts` 钉 `use.locale: "zh-CN"`(**必须与 detector 同提交落地**,否则 CI chromium 英文环境令 31 个中文定位器全崩);关键路径 ~25 元素加 data-testid,smoke 逐页批次同步;C7 起新增 en 冒烟 spec(localStorage 预置 en-US,断言公开页无 CJK 残留/无 `xxx.yyy` 键泄漏)。

## 数据变更

无 DB 迁移。错误响应体新增 `message_key`/`params` 字段(增量,旧客户端忽略即兼容);`task openapi` 验证 openapi.json 无 diff。

## 分批(C1~C14,每批独立过闸可回滚;fallbackLng=zh 保证中途态 zh 用户零感知)

| 批 | 内容 |
|---|---|
| C1 | web 基建:依赖三件套 + i18n.ts/locale.ts/LangSwitcher(DEV)+ AppProviders 收敛(错误页/404 六条文案即迁)+ i18next.config + typegen 产物 + locales.test + turbo/Taskfile/CI 接线 + **playwright locale 钉 zh-CN** |
| C2 | packages/ui 状态表 labelKey 化(破坏性,web+admin 消费点同提交迁;**admin 同批挂最小 i18n 运行时**——仅 shared/errors 资源,否则裸显 key)+ shared.json + ui locales.test |
| C3 | format 七函数新签名 + shared.json format 段 + 双语参数化测试 + 全部 ~63 调用点(模块级列定义/常量下移组件体;ECharts option 的 t 进 useMemo deps 由 react-hooks lint 强制) |
| C4 | web 公开层(landing 四 section/页脚/顶栏/login)+ marketing.ts 删除 + smoke 注册段 testid 化 |
| C5 | web 实例域(列表/详情/InstanceActions/SERIES_META/ECharts)+ smoke 实例段 |
| C6 | web 市场与存储(market/create/skuTable/CheckoutBar/storage)+ **copy.ts 删除** + smoke 市场段 |
| C7 | web 账务/设置收尾(billing/dashboard/settings/QueryState/TopBarUser/CSV 表头)+ web CJK 闸门激活 + en 冒烟 spec + LangSwitcher 放开 |
| C8 | 后端 message_key 机制(errors.py/messages.py/导出脚本/CI no-diff)+ mutator/apiErrorText/useApiErrorText + web mutations.ts 接入 + 机制 pytest |
| C9 | 错误 key 化批 1:orchestrator/disks/catalog/metering(~26 处) |
| C10 | 错误 key 化批 2:account/billing 四文件(~45 处) |
| C11 | 错误 key 化批 3:adminapi/nodes/core 收尾(~20 处)+ 5 处中文断言清零 |
| C12 | admin 基建 + 壳层(菜单/ROLE_LABEL/login/ReasonAction 语序重写 `t("common.actionDone",{action})`/AuditTable) |
| C13 | admin 运营页(index ECharts/nodes PHASE_LABEL/skus/images/tenants) |
| C14 | admin 财务/配置收尾(finance DatePicker 双同步人工验证/audit/platform 四表译+GROUP_INTRO 豁免/settings)+ admin CJK 闸门激活 + CLAUDE.md 补「新文案必须走 i18n key」 |

C8~C11 可与 C4~C7 交错;C13/C14 为文案密集整屏批,接近但不超 2000 行上限,超则按页对半拆。

## 验收用例

1. `pnpm i18n` 全绿:extract 无新增键、types 无 diff、lint 0 硬编码;locales.test zh/en parity 全过;check-cjk 对已激活端 0 命中(白名单外)。
2. 切语言(web 顶栏):antd 组件文案/日期选择器/状态徽标/金额符号/时长量词/菜单/表格列/错误弹窗全部跟随;localStorage 记忆,刷新保持;html lang 同步。
3. 后端:`AppError(key=..., params=...)` 响应含 message_key/params 且 message 为渲染后中文;存量 `AppError(code, "中文")` 行为不变;422/500 带兜底 key;缺键 render 回落 key 不 500(pytest)。
4. 前端错误展示:mock 一个带 message_key 的 4xx → en 环境显示英文、zh 显示中文;无 key 的旧错误体回落 message。
5. e2e:zh 主链路 smoke 全绿;en 冒烟 spec(/login /market 无 CJK、无键泄漏、切换器可用)。
6. 外科手术清单逐项销账(spec 本节勾选);禁词 grep 0 命中。

## 人工事项

- 阿里云英文短信签名/模板报备立即启动(周级周期);大陆签名仅中文,国际短信需 SendMessageToGlobe 另评估。
- legal.terms/privacy 英文版法务起草审定(工程仅预留 per-locale 整页资源位)。
