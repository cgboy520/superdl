# SuperDL UI/UX 规格

两端(`web` 用户控制台与公开层 / `admin` 管理控制台)的信息架构、逐屏交互规则与视觉约定。文案规范见 [`copy-style-guide.md`](./copy-style-guide.md),前端契约见 [`reference/web.md`](./reference/web.md) 与 [`reference/admin.md`](./reference/admin.md),共享组件的落点在 `packages/ui/src/components/`。

## 1. 全站交互规则

1. **决定优先于说明**:常驻横幅只放「有时效、可行动」的事:到期 / 冻结 / 失败 / 低余额 / 欠费 / 公告 / 部分数据加载失败 / 配置风险。**一页至多一条横幅**,任何页面 ≥2 条 `Alert` 必须经共享 `AttentionBar` 聚合为「N 件需要处理」+ 展开列表,严重度取最高(用户端实例列表、费用中心;管理端总览、集群、平台配置、节点)。政策与口径说明进标题旁 `?` tooltip、「计费规则」弹窗或卡内脚注(≤30 字),不做常驻条。合规声明(禁挖矿)只在公开页脚与市场页脚。
2. **一处一主动作**:每页一个 primary CTA,放在 `PageHeader.extra`。表格行内动作走 `RowActions` 三个槽位:`主动作`(随状态变)+ `次动作`(随状态变)+ `更多 ▾`(`RowMoreMenu`,危险项末尾、标 `danger`);槽位只能少不能多。进详情走名称链接;**禁止双击行**。
3. **CTA 即库存**:首页行情板与价格墙的按钮直接写「可开实例 N」/「已租完」;市场表格用「可开实例」列 + 售罄行弱化底色(不用 opacity)并排到末尾。SKU `available_count` 是近似可开实例数(共享档含超卖系数),**不是物理空闲卡数**;页面一律用「可开实例 / 台」表述,禁止换算成「卡」。售罄从不是死胡同:售罄行 / 卡片给「看同型号其它档位」链接。
4. **条件操作一律可见但禁用 + 原因可读**:禁用带原因的按钮唯一写法是 `GatedButton`(`reason` 非空 → `aria-disabled` + `tabIndex=0` + 拦截点击 + Tooltip 原因;键盘 Tab 可达)。`Tooltip` 直接包原生 `disabled` Button 由 **ESLint 禁止**;原生硬禁用只用于「提交在途」。菜单项(`RowMoreMenu`)同样永不隐藏条目,灰置项也用 `GatedButton` 渲染。占位项的去留有判据:只有「已排期、按当前设计确定要做」的能力留 disabled 占位并注「即将上线」;兑现或删除时 [`reference/web.md`](./reference/web.md) 与本节占位清单同提交更新。
5. **价格口径显性化**:表头写清「单卡 ¥/时」或「整机 ¥/时」;卡数 >1 时行内副行给 `× N = 总价`;结算条大字带 `× N 卡` 后缀;包周期「原价 / 优惠 / 应付」直接摊在结算条第二行,不进 Popover;非金额项(到期时间)降级为正文字号。「数据盘费用(按日)」单独一栏(关机也扣的钱),无盘不出;与「今日消费」严格分词。
6. **风险前置、同意一次**:选中「共享·经济」或「竞价」的那一刻,在 chip 下方给 ≤3 行风险摘要;提交时只弹**一个**分节知情同意弹窗(`ConsentGate`:竞价一节 / 经济一节,命中几节出几节),每节 ≤3 条 + 「完整说明」链接,一次勾选;创建失败重试不重置勾选;确认后先执行 `onProceed` 再关弹窗,按钮 loading 真实可见。竞价五条与经济四条的完整文案见 §3.5,②③④ 逐条对应 `apps/api/app/modules/orchestrator/preempt.py` 的三条硬规矩与结算口径,**改代码等于改文案,两边同提交**;折扣与宽限秒数从 `/policies` 读,不硬编码。
7. **关机 ≠ 释放,多级删除防护**:释放需**键入实例名称** + 勾选「我确认将清除实例盘全部数据(数据盘不受影响)」两道闸才解锁红色按钮。`creating` 态入口文案「取消创建」,只过键入这道。删除数据盘同为两道闸(键入盘名 + 勾选「盘内数据将被清除」)。
8. **确认强度分级(L0~L3),两端强制组件化**:L3 = `TypeConfirmModal`(键入目标名 + 勾选;释放实例、删除数据盘、删除服务、注销账号、管理端执行注销);L2 = `useConfirm`(后果前置 + 影响说明;关机、重启、SKU 改价、群发公告、重新生成注册命令、重新生成恢复码、改管理员角色、登出全部设备)或管理端 `ReasonAction`(原因必填 → 二次确认 → 审计;两步都显示目标标识);L1 = `useConfirm` 轻量(可逆且影响面 = 1:删 SSH key、解决 / 关闭工单、归档法务草稿);L0 = 无确认(开关类可逆操作、退出登录)。**恢复方向的管理动作(解封节点、解冻租户、上架 SKU)只填原因、不做第二步确认**(不倒挂:恢复不能比破坏更难)。**`Popconfirm` 全站禁用**;危险动作的确认按钮一律 `danger`;审计型原因一律手输。确认文案 = 「标题问句(含目标)+ 后果正文」。
9. **给等待路径,不给死胡同**:库存不足给「换个规格」引导(结算条 `notice` 常驻,不用 toast);创建失败给「重新创建」按钮与失败原因;建盘成功而建实例失败,用页内不自动消失的 `Alert` + 「去存储页」按钮。
10. **URL 即状态**:列表筛选、搜索词、Tab activeKey、深链目标(节点名 / 工单 id / 租户 id / 配置分组 / 对账日期)一律入 URL(`validateSearch` 白名单 + 默认值剥离 + `replace: true`);控件与 URL 双向同步(`useUrlFilters` / `useUrlCommittedInput`);抽屉开合若承载可转达视图(租户抽屉、节点详情)也入 URL。**所有详情页「返回列表」带回列表最近筛选态**(web `stores/listSearch`),实例、服务、工单一致。
11. **轮询三律 + 新鲜度可见**:① 只经 react-query `refetchInterval`,周期取 `packages/ui/src/polling.ts` 的 `POLL`,禁止裸数字与原生 `setInterval`;② 一律函数式:过渡态 `POLL.transient`、稳态 `POLL.steady`、终态即停(false);③ `useInfiniteQuery` 上禁止轮询,列表新鲜度靠 `refetchOnWindowFocus` + 手动刷新;折叠 / 未打开的 UI 对应查询挂 `enabled`。**凡有轮询的页面必须在页头出新鲜度条**(`PageHeader.freshness` + `useAutoRefresh`:「更新于 · 每 N 秒自动刷新 · 暂停 / 立即刷新」),两端一致;局部面板用独立导出的 `Freshness`。
12. **密度分级**:web「舒适」(表格 `cellPaddingBlock 12`、正文 14);admin「紧凑」(表格 13px / `cellPaddingBlock 8`,嵌套表 `size="small"`,顶层表不用 `small`);全站 `tabular-nums`。**表格规范(两端)**:数值与金额列右对齐;标识列(uuid / 订单号 / slug / 节点名)用 `Mono`;`scroll.x ≥ 1000` 的表**必须**固定标识列(左)与操作列(右)+ `sticky={{ offsetHeader: layout.topBarHeight }}`,固定右列必须是最后一列;宽表页用 `PageContainer width="full"`;空态一律 `EmptyState`(筛选无结果用 `search` 场景 + 「清除筛选」次动作),错误态 `TableErrorEmpty`;游标分页表一律 `CursorTable`。
13. **尺寸与容器分档**:输入框 / 下拉宽度取 `controlWidth`(xs 96 / sm 160 / md 260 / lg 320);≥2 张卡或需滚动的编辑表单改 Drawer(`drawerWidth.md/lg` 两档,提交与取消固定在 `footer`,挂 `useLeaveGuard`,审计原因是最后一个字段);按钮尺寸「页头 CTA middle / 结算条 large / 卡内 middle / 行内 small」;锚点滚动目标加 `scroll-margin-top: layout.scrollMarginTop`。
14. **「选一个」控件角色表**:`Segmented` = 视图 / Tab 切换(状态计数条、监控范围、登录方式);`ChipRow` = 筛选与轻量选项(市场筛选、GPU 数量、计费方式);`OptionTile` = 表单里的互斥大项(镜像、数据盘模式、充值渠道、访问鉴权、协议),`role=radiogroup` + 方向键;表内 radio 只用于「从列表里挑一行」。禁止 `Button + aria-pressed` 表示选中,禁止 `Radio.Group optionType="button"`。
15. **可访问性底线**:两端 `<main id="main">` 地标 + 跳转链接;导航当前项 `aria-current="page"`;手写 `role="button"` 元素统一焦点框(`base.css .focus-ring`);Popover / Tooltip 信息触屏可点开(`trigger` 含 click,宿主可聚焦);快捷键在可编辑元素聚焦时不抢;告警严重度与状态从不只靠颜色(文字 + 图标);`HexTag` 文字色按底色亮度取黑 / 白;移动端也能切主题(用户菜单)。
16. **页面骨架**:每个控制台页面自持 `PageContainer`(`title` / `description` / `extra` / `freshness` / `width`),下方可选 `FilterBar`(筛选控件 + 「清除筛选」+ 「共 N 条」),再是主体;两端一致(壳只提供顶栏 / 侧栏 / `<main>`,不包容器)。实体详情页与抽屉头部用 `EntityHeader`。闸门:`python3 scripts/check-page-skeleton.py`。

## 2. 视觉与主题

|          | 用户端 `web`                                                                                                                                                                                                                                                                 | 管理端 `admin`                                                                                                                                                                                                                           |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 基调     | 浅色(默认)/ 暗色(深靛灰 `#0F1420` 系,顶栏图标钮与用户菜单「主题」项切换,`localStorage("superdl.theme")` 持久化,初值跟系统);**公开层同样跟随主题**                                                                                                                            | 深色 NOC 风                                                                                                                                                                                                                              |
| 主色     | 靛蓝 `#4F46E5`;暗色下主色文本取 `#A5B4FC`                                                                                                                                                                                                                                    | 同主色;亮青 `#22D3EE` 数据强调、琥珀 `#F59E0B` 告警                                                                                                                                                                                      |
| 品牌渐变 | 只用于公开顶栏 / 主页 Hero / CTA 横幅与登录页左栏;控制台顶栏**中性色**(与侧栏同底 + 下边线),渐变底上的反白 CTA 走 `brandInverseButtonStyle`;公开层另有深墨面板 `brand.ink`(行情板 / 页脚)                                                                                    | 不用渐变;prod 环境顶栏加 3px 红色上边线 + 红色环境徽标(判定收在 `lib/environment.ts` 的 `useEnvironment()`;后端暂无端点暴露 environment,现按构建模式判定,接口补上后只改这一处)                                                           |
| 实现     | antd 6 ConfigProvider token + components 级 token;暗色 = `theme.darkAlgorithm` + `webDarkTheme` 覆写,定义在 `packages/ui/src/tokens.ts`;全局 reset / 工具类在 `packages/ui/src/base.css`(两端各 import 一次)+ `apps/web/src/styles.css`;`index.html` 内联脚本预置底色防 FOUC | antd `theme.darkAlgorithm` + 自定义背景 `#0B1220` 系(色值集中在 `adminColors`);表格密度由 `adminThemeComponents.Table` 统一                                                                                                              |
| 字体     | 系统栈(`tokens.fontFamily`)+ body 级 tabular-nums;标识 / 价格 / 命令用 `fontFamilyMono`(公开层自托管 IBM Plex Mono 子集,放在 web 的 public 目录下,控制台回落 ui-monospace)                                                                                                   | 同左;标识符列 `Mono`                                                                                                                                                                                                                     |
| 布局     | 中性顶栏 56px(logo \| 余额 · ⌘K · 通知铃 · 主题切换 · 用户菜单)+ 侧栏 `layout.siderWidth`(lg 以上常显;窄屏不渲染侧栏,导航走顶栏汉堡 Drawer `layout.navDrawerWidth`,与侧栏共用 `ConsoleNavMenu`)+ `<main>` 内容区,页面自持 `PageContainer`(default 1280)                      | 侧栏 200px(分组;按角色过滤;桌面可手动收成 80px 图标轨,收起态点图标导航;窄屏收为 0 宽走汉堡 Drawer,有遮罩、Esc 关闭)+ 56px sticky 顶栏(环境徽标 \| ⌘K 触发器 · 语言(图标下拉)· 告警铃(底部「查看全部」「全部确认」)· 用户名与角色 ▾ 退出) |
| 状态色   | running 绿 / creating·starting 蓝 / stopped 灰 / frozen 橙 / failed·releasing 红;徽标可带图标(check / sync / pause / warning / close)                                                                                                                                        | 同一套语义色,深色版调亮;告警严重度 critical 红 / warning 琥珀 / info 蓝,徽标 = 图标 + 文字                                                                                                                                               |

**设计 token 纪律**:`packages/ui/src/tokens.ts` 是唯一事实源。
① 色值走 token(`webTheme` / `webDarkTheme` / `adminColors` / `statusColors` / `themeColors`),禁止硬编码 hex(ESLint);**JS 侧取语义色只经 `useThemeColors()`**(web 按主题给 `web-light` / `web-dark`,admin 固定 `admin`),禁止在组件里 `import { colorPrimary }` 或分支 `useThemeMode`;图表主题经 `useChartTheme()`;
② 布局尺寸走 `space`(4 阶梯)与 `layout`(含 `topBarHeight` / `scrollMarginTop` / `siderWidth` / `siderCollapsedWidth` / `navDrawerWidth`),字号走 `fontSize`(pageTitle / sectionTitle / body / caption / kpi / display),图标走 `iconSize`(sm 14 / md 16 / lg 20),控件 / Drawer 宽度走 `controlWidth` / `drawerWidth`;`Space size` 只取 `space.*`;
③ 高频模式组件化(`packages/ui` `src/components/`):页面骨架 `PageContainer` / `PageHeader` / `Freshness` / `FilterBar`(+ `useUrlFilters` / `useUrlCommittedInput`)/ `EntityHeader`;数据 `KpiGrid` / `StatCard` / `KeyValue` / `CursorTable` / `TableErrorEmpty` / `DataErrorAlert` / `EmptyState` / `EmptyValue` / `Mono` / `CopyField` / `InlineEdit` / `EChart`(三态 + `group` 联动);状态 `StatusTag`(tag / badge / dot / text 四形态,带 hint 与可选图标)/ `HexTag` / `StatusSummaryBar` / `TriageBar` / `AttentionBar`;动作 `GatedButton` / `RowActions` / `RowMoreMenu` / `DangerZone` / `LoadMore`;表单 `ChipRow` / `OptionTile` / `SectionRail`(+ `SectionAnchor` / `deriveSectionStatus`)/ `DiskSizeField`;钩子 `useLeaveGuard`(核心无路由依赖,各端 3 行包装注入 `useBlocker`)/ `useAutoRefresh` / `useThemeColors` / `useChartTheme`。用户端另有 `Field` / `SmsCodeField` / `LandingSection` / `CheckoutBar` / `ConsentGate` / `OnboardingSteps`,管理端另有 `ReasonAction` / `BulkBar` / `ListCapNote` / `AuditTable`;
④ 确认强度组件化:L1 / L2 用 `useConfirm`(支持 `danger` / `okDisabled`),L3 用 `TypeConfirmModal`,管理端审计型用 `ReasonAction`(内部用 `GatedButton`);
⑤ 动效走 `motion` token(fast 0.15 / normal 0.2 + easeOut):仅透明度 / 位移,路由切换不动效;自绘浮层 zIndex 走 `zIndex`(stickyBar / topBar / skipLink);
⑥ CSS 覆盖区一律 `var(--sdl-*)`,取值经 `cssVars` 桥由 `__root.tsx` 注入;admin 端走 `var(--admin-*)`(`main.tsx` 从 `adminColors` 注入);新代码不写 inline 尺寸魔法数;
⑦ 对比度底线 WCAG AA ≥4.5:1(文本)/ 3:1(图形),新增色值在 `tokens.test.ts` 补回归;`HexTag` 用 `color.ts` 的 `textOnColor` 取字色;
⑧ 底色类 token 改动按 tokens.ts 顶部同步清单核对防 FOUC 位置(两端 index.html、`__root.tsx`);web 端 index.html 内联脚本任何改动同步重算 CSP sha256(`scripts/check-csp-hash.sh`)。

## 3. 用户端

### 3.0 公开层

设计定位:面向中国 ML 工程师与小团队的 GPU 租用;差异点「价格即库存 · 按秒计费 · 数据盘独立」。**首屏是行情板,不是海报**:数字一律取真实数据,没有泛营销模块。

**主页 `/`**(public 顶栏:品牌渐变;logo | 算力市场 / GPU 价格 #pricing / 算力排名 #ranking / 帮助 | 主题 / 语言 / 登录 / 免费注册(反白 CTA);已登录换「进入控制台」→ `/instances`;<lg 汉堡 Drawer 同一份链接):

```
┌ [S SuperDL]  算力市场 · GPU 价格 · 算力排名 · 帮助        ☾ 中文▾ 登录 [免费注册] ┐
│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒│
│  SuperDL GPU 算力云              ┌ 实时行情        12 秒前更新 ┐   │
│  按秒开机、按量计费,             │ H100 SXM 标准  ¥2.50/时 可开16台 [租用]│
│  价格牌上的库存就是真库存         │ RTX 4090 整卡  ¥3.99/时 可开16台 [租用]│
│  [免费注册] [查看算力市场]        │ RTX 4090 经济  ¥0.99/时 可开180台[租用]│
│                                  │ H100 SXM 标准  ¥2.00/时 已租完 看其它档位│
│                                  └ 查看全部规格 → ─────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│ 怎么计费   ┌按秒累计┐ ┌关机停表┐ ┌数据盘独立┐                          │
│ 开机 ──●━━━━ 运行中(GPU 时费 ¥/时×卡) ━━●── 关机 ┈┈实例盘保留┈┈ ● 释放  │
│ ━━━━━━━━━━━━━━━━━━ 数据盘 按日 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
├──────────────────────────────────────────────────────────────────────┤
│ GPU 租用价格(卡片内按档位分行,每行 CTA 即库存;售罄行 → 市场同型号)   │
│ GPU 算力排名  [FP16][FP32][每 TFLOPS 时价]     三步开机     CTA 横幅   │
└──────────────────────────────────────────────────────────────────────┘
```

1. **Hero**:左侧标题(`fontSize.display`,<lg 降到 `fontSize.kpi`)+ 一句话 + 两个 CTA;右侧**行情板**(`brand.ink` 面板,内部套 `ConfigProvider` dark 算法,antd 子件自行按深底取色):按 (型号 × 档位) 取代表 SKU 前 5 行(有货优先,其次单价低),每行 型号(`fontFamilyMono`)· 档位标 · 单卡 / 整机 ¥/时(等宽大字)· 「可开 N 台」(`themeColors["web-dark"].positive`)+ 「租用」→ `/market?model=&sku=`,或「已租完 · 看其它档位」→ `/market?model=`(**售罄行不摆禁用按钮**);头部「更新于 N 秒前」(`POLL.publicBoard` 60s 轮询,`Freshness intervalMs={false}`,不显示周期);接口失败整板降级为「前往算力市场」;**<lg 行情板整块落到 CTA 下方(行内换行,不横向滚动),不消失**。
2. **怎么计费**:三条事实(按秒累计 / 关机停表 / 数据盘独立)+ 计费时间轴 SVG(开机 → 运行中计费 → 关机停表 → 释放;数据盘按日通贯;实例盘保留段虚线)。
3. **GPU 价格墙**(`/skus` 公开端点,`POLL.publicBoard`):按型号一张卡,卡内按档位分行(专用整卡 / 共享·标准 / 共享·经济),每行价格 + 「可开 N 台」CTA → `/market?model=&sku=`(不直落创建页);售罄行链到 `/market?model=`;接口失败整区降级为「前往算力市场」。
4. **算力排名**:`packages/ui/src/gpuSpecs.ts` 静态表驱动;`Segmented` 三档 FP16 / FP32 / **每 TFLOPS 时价**(只算在售型号,时价取该型号最低单卡价 ÷ FP16 峰值,真实数据,越便宜条越长);前三名奖牌序号,横向百分比条,在售型号带标记链到 `#pricing`,脚注按档切换(算力档「理论峰值算力」/ 性价比档说明口径);窄屏行内换行**并保留单位**。
5. **三步开机**(`#quickstart`;充值 → 选规格 → SSH 连接):每步一条真实入口(费用中心 / 算力市场),第三步给命令形状(`ssh -p <端口> root@<主机>`,墨色块 + 等宽,与帮助页、实例详情同一口径,不编造主机名)。编号是真序列,不是装饰。
6. **CTA 横幅**(真实可开台数)+ 三栏页脚(产品 / 支持(含帮助)/ 合规 + 防挖矿声明;ICP 与公安备案号取自 `/site-config`,缺失不渲染)。
7. 整层跟随主题(底色取 `colorBgLayout`);Hero 渐变与墨色面板两种主题同一套;正文区包在 `<main id="main">` 里。

**登录 `/login`**:分屏。左 45% 品牌渐变区(lg 以下隐藏)显示**真实行情摘要**(最低时价 / 当前可开 N 台 / 「按秒计费,关机停表」),不放口号列表;右侧表单:**字段带可见标签**(手机号 / 短信验证码 / 密码 / 新密码),**登录与注册分离** —— 登录态标题「登录 SuperDL」+ Segmented 二选一(验证码 / 密码,+86 前缀)+ 底部「没有账号?免费注册」,「忘记密码」在两种模式都可见;注册态标题「注册 SuperDL」+ 底部「已有账号?去登录」;重置密码态底部「返回登录」。`?mode=register` 直达注册态。`?redirect=` 按 `new URL(r, origin)` 解析,只接受同源且 pathname 不以 `//` 开头的目标,归一化为 `path + search + hash`。

**帮助 `/help`**:帮助中心 IA —— 顶部「快速开始」卡(三步 + 命令)+ 搜索框(过滤问答);≥lg 左侧分类锚点(连接 / 计费 / 数据 / 故障),<lg 顶部横向 chips;问答展开(`#faq-<key>` 直达即展开并滚动),每条尾部「没解决?提交工单」(登录 → `/support`,未登录 → 登录带回跳);联系卡(来自平台配置,未配置不展示)。

**法务 `/legal/*`**:顶部 Segmented 切换三份文档(用户协议 / 隐私政策 / 注销须知)+ 版本行置顶;≥lg 右侧 h2 目录;打印样式;英文回落提示保留;正文取后端当前 published 版。

### 3.1 信息架构

```
公开层    /(首页)· /login · /help · /legal/terms · /legal/privacy · /legal/deletion-notice
控制台(中性顶栏 + 分组侧栏;事实源 apps/web/src/components/layout/consoleNav.tsx)
├─ 资源
│  ├─ 容器实例  /instances   ← 登录后默认落地页(CONSOLE_HOME);只列开发机;真空态 = 整页新手引导;页顶 AttentionBar + 状态计数条
│  ├─ 在线服务  /services    # /services/new 部署,/services/:slug 详情(?tab=)
│  └─ 存储      /storage     # 数据盘 + 挂载全景
├─ 购买与账务
│  ├─ 算力市场  /market      ← 未登录可看,下单跳登录
│  └─ 费用中心  /billing     # 余额/充值/账单/收支明细/退款/发票
└─ 支持        /support     # FAQ / 联系客服 / 我的工单(/support/:ticketId 对话流)
不在主导航(经顶栏到达)
   通知中心    /notifications # 铃铛 → 「查看全部」/ 用户菜单;全部/未读筛选 + 行点击已读并跳转 + 全部已读
   账户设置    /settings      # 用户菜单;四 Tab:SSH 公钥 / 通知 / 实名认证 / 账号(?tab=)
创建实例     /market/create/:skuId   ← 全页路由(侧栏高亮「算力市场」)
```

无独立概览页:KPI 由顶栏余额与费用中心承接,公告 / 余额预警 / 欠费聚合进实例列表页顶的 `AttentionBar`,「一眼看全」由实例列表的 `StatusSummaryBar` 承担,新手引导落在实例列表真空态。

**顶栏右区**(`TopBarUser`):余额(→ 费用中心)· ⌘K(带 kbd 徽标,md 以上)· 通知铃(Popover:最近通知 + 全部已读 + 查看全部)· 主题切换(md 以上)· 用户菜单(账户设置 / 通知中心 / 帮助 / 主题 / 语言子菜单 / 退出)。窄屏(<md)只留余额 / 铃 / 用户,主题与语言经用户菜单到达。

**侧栏**(`ConsoleNavMenu`):多项分组出组标题(资源 / 购买与账务),单项分组前出分隔;整行可点(`Menu.onClick` 导航,label 仍是 Link 以支持中键 / 新标签,`aria-current="page"`);选中态为最长前缀匹配,`/settings` `/notifications` `/help` 不高亮任何项。

**命令面板(Cmd+K / Ctrl+K)**:顶栏触发器 + 全局快捷键;命中范围 = 控制台页面导航(含不在主导航的通知中心 / 账户设置 / 帮助)/ 实例(列表缓存前 100 条,名称与 uuid 模糊)/ 在线服务 / 快捷动作(部署服务、租用新实例、充值、新建工单);双语关键词;无实例缓存时不渲染实例分组。**全局快捷键**:`/` 聚焦当前页搜索框;`g i` / `g s` / `g b` / `g m` 两键导航到实例 / 服务 / 费用 / 市场;输入框聚焦时不触发。

### 3.2 屏 ① 容器实例列表

```
┌ 容器实例                      12 秒前更新 · 每 45 秒 ⟳   [ 租用新实例 ] ┐
│ 开发机;在线服务在「在线服务」页                                      │
├──────────────────────────────────────────────────────────────────────┤
│ [ 全部 12 | 运行中 5 | 已关机 4 | ●需处理 3 ]      [🔍 搜索名称 / ID ] │
├──────────┬────────┬──────────────┬──────────┬──────────────┬─────────┤
│ 名称 / ID│ 状态   │ 规格         │ GPU 利用率│ 费用         │ 操作 ⇥  │
├──────────┼────────┼──────────────┼──────────┼──────────────┼─────────┤
│ train-…  │ ●运行中│ H100 ×2 标准 │ ▁▂▅▇ 62% │ ¥5.00/时 ×2  │[连接▾][关机][更多▾]
│ month-…  │ ○已关机│ H100 ×1 标准 │ —        │ 包月·剩 29 天│[开机 ][事件][更多▾]
│ spot-…   │ ◐已冻结│ RTX4090 ×1   │ —        │ 竞价 ¥0.40   │[开机⃠][事件][更多▾]
│ eval-…   │ ✕失败  │ RTX4090 ×4   │ —        │ ¥15.96/时    │[重新创建][事件][更多▾]
└──────────┴────────┴──────────────┴──────────┴──────────────┴─────────┘
```

- **页头**(`PageContainer`):标题 + 描述「开发机;在线服务在「在线服务」页」+ 右侧「刷新」(新鲜度条:指标 `POLL.metrics`、今日消费 `POLL.daily`)+ `租用新实例`(唯一 primary,middle)。
- **AttentionBar**(页顶唯一横幅,规则 1):条目来源 = 未读通知(公告 / 余额预警 / 欠费)+ 到期实例(`GET /api/v1/instances/expiring?within_days=N`,N 取 `policies.period_expire_warn_days`,最多 3 条,带 `立即续费` 与首条的 `开启自动续费`)+ 当前页冻结实例(带回收倒计时 + `去充值`)+ 当前页失败实例(带 `查看事件`)。1 条直接显示;多条折成「N 件需要处理」+ 展开列表,严重度取最高。
- **状态计数条 + 搜索**(`StatusSummaryBar` + `FilterBar`,同一行):`全部 / 运行中 / 已关机 / 需处理`(需处理 = creating ∪ starting ∪ stopping ∪ frozen ∪ failed ∪ 即将到期),点击写 `?status=`(白名单含 `attention`,客户端解析为状态集合);计数取自已加载行,**全部加载完(无下一页)才显示数字**;搜索框客户端防抖 300ms(name / uuid)入 `?q=`。冻结策略说明在 stopped 徽标的 tooltip 与「计费规则」弹窗里,不做常驻条。
- **表格 6 列**(固定名称列 + 操作列,sticky 表头):①`名称 / ID`:名称即详情链接,hover 出铅笔进入行内改名(`InlineEdit`,Enter 保存 / Esc 取消),第二行 uuid 前 12 位 `Mono`;②`状态` `StatusTag badge`(冻结态附红色回收倒计时,stopped 态 tooltip 出冻结策略;宿主可聚焦、可点);③`规格` GPU 型号 × 数量 + 档位徽标,Popover(hover / focus / click 三触发)展开完整配置;④`GPU 利用率` sparkline(近 1h,running 时;`GET /api/v1/metrics/instances` 批量端点)+ 末值 %,断源灰字「监控暂不可用」;⑤`费用` 随购买模式分化:按量 `按量` 标签 + `¥X.XX/时 × N 卡` + 第二行 `今日 ¥Y.YY`(`/bills/daily-summary`);包周期 `包月 · 剩 23 天` + 第二行 `¥X/月`,剩余天数由 `subscription.expires_at` 算,临期警示色、已到期写「已到期」,**不显示今日消费**;竞价 `竞价` 标签 + `¥X.XX/时` + 「可回收」标记,今日消费**照出**;⑥`操作`(`RowActions`,固定右):**主动作随状态** —— running `连接 ▾`(primary;菜单:复制 SSH 命令 / 打开 JupyterLab / 连接信息 / 实例监控,access 只在菜单打开后拉取)、failed `重新创建`(primary,链到同规格创建页)、其余 `开机`(primary;不可用时 `GatedButton` 带原因:需先关机 / 欠费请充值 / 包周期已到期);**次动作随状态** —— running `关机`、其余状态 `事件记录`;`更多 ▾`(`RowMoreMenu`)。
- 窄屏(<md)换卡片流,同一套单元格组件;其它列表不做卡片流。
- 「更多 ▾」条目与顺序:`重启` / `事件记录`(running 时;其它状态已在次动作)/(包周期)`续费`·`自动续费` /(竞价)`转按量` /(按量)`转包周期` / `释放实例`(红;`creating` 态「取消创建」)。占位清单为空。
  **`转包周期`** 只对 running / stopped 的按量实例可用。它**是一次性预扣的支付动作**:modal 与续费同形(周期 chip + 数量 + 费用明细 + 余额变化 + 新到期时间),必须写清**转换前那段按量费用会先结清**、转换后**中途释放不退款**、周期从**现在**起算。转换失败分开说明:结算追平中(稍后再试)与该规格不支持包周期(换规格)。
  **`转按量`** 只对竞价实例可用;确认弹窗必写的两条(转换后不再被回收、当前整点小时整体改按按量价结算)与「不带 `Idempotency-Key`、已是按量原样返回 200」见 [reference/web.md](./reference/web.md)。
  不放:更换镜像 / 重置系统 / 升降配置 / 修改 SSH 密码 / 扩缩数据盘(归存储页)/ 保存镜像。
- 不做的列:地区 / 主机号、本地磁盘 %、镜像状态、付费方式独立列、释放时间独立列。
- 关机二次确认(L2,红色确认钮):按量实例说明 GPU 释放、再开机可能库存不足;**包周期实例的关机确认写清**周期内关机不退费,库存为其保留。释放走规则 7;包周期实例的释放确认额外写明**预付不退款、剩余天数作废**。
- **续费 modal**(列表与详情共用):`当前周期` / `续费时长`(四个周期 chip + 数量,可与当前不同)/ `费用明细`(实例费用、周期折扣、应付)/ `可用余额`(当前 → 扣后)/ `新到期时间`。确认前明细是 `/policies` 折扣算的**预览值**,确认后以响应 `quote` 为准并在成功提示给出实扣金额;每次打开 modal 生成一个 `Idempotency-Key`。**提前续费从老到期时刻起算**,modal 里写出来。
- 真空态(无筛选且无实例)= **整页替换**为新手引导(不渲染表头):三步(充值 → 选规格 → 创建,有已支付充值时第 1 步置完成)+ `去算力市场`;筛选无结果 `EmptyState(search)` + 「清除筛选」;查询失败错误态优先。

### 3.3 屏 ② 实例详情

`EntityHeader`(面包屑「容器实例 › {name}」带回列表最近筛选态):名称(铅笔行内改名)/ 状态徽标 / 档位 + 包周期 `包月 · 剩 23 天` / 竞价标 + 元信息条(ID 完整 uuid `Mono` + 复制 / 规格 / 单价 / 今日消费(包周期换「到期时间」)/ 创建时间)+ 操作组(同列表,middle 尺寸);订阅已到期时开机按钮 `GatedButton`「包周期已到期,请先续费」。窄屏自动换行:名称行 → 标签行 → 元信息 → 操作。

Tab 固定 `连接 / 监控 / 日志 / 事件 / 账单 / 设置`(白名单常量 `DETAIL_TABS`,`?tab=` 直达,非法值回默认)。**默认 Tab 按状态**:running → 连接;其余 → 事件。

- **连接**:SSH 卡片(完整登录指令 `CopyField` + 仅密钥登录说明)+ JupyterLab 卡片(打开 + token 重置)。在线服务的版本实例按 `with_ssh` 决定是否渲染 SSH 卡片,Jupyter 卡片不渲染。
- **监控**:GPU 利用率 / 显存 / CPU / 内存 **2×2 栅格**,四图 `axisPointer` 联动(`EChart group`);工具行 sticky(范围 Segmented 1h/6h/24h + `Freshness`);`%` 类固定 0~100,MB 序列超 1 GB 整图换 GB;每图右上「当前 / 峰值」。**非 running 仍可查历史**(不自动刷新,标「显示历史数据」);running 时 `POLL.daily` 轮询;断源(503)显示「监控数据暂不可用,不影响计费」;其余错误可重试,不渲染成空图。
- **日志**:容器日志末 N 行 + 行数选择 + 关键词过滤(只作用于已拉取行,给「匹配 M / N 行」)+ 换行开关 + 自动刷新(`POLL.logs`)开关 + 下载 `.log`;贴底跟随 + 新行计数;截断注明「仅显示末尾 N 行」;非 running/stopping 提示「实例运行中或关机中才能读取容器日志」。
- **事件**:状态迁移时间线(状态走 `instanceStatusMap` 翻译;running 两侧带 `计费边界` Tag;时间 / 事件 / 操作者);「此记录即计费依据」放工具行 tooltip;Segmented「全部 / 只看计费边界 / 只看失败」(只作用于已加载页);reason 映射必须覆盖 `preempted`。
- **账单**:该实例小时账单表(`CursorTable`,单价 / 金额右对齐)。
- **设置**:只有危险区(`DangerZone`:释放实例,前置条件经 `GatedButton`);改名在头部完成。

### 3.4 屏 ③ 算力市场

- **页头**:标题 + 右侧 `?` 图标按钮「计费规则」(modal,middle)。无常驻合规条:禁挖矿声明只在本页页脚一行小字。
- **版面**:「选择规格」卡 → 底部结算条。计费方式不单独成卡:`BillingModeCard` 的 chip 行(`role=group` 名「计费方式」)位于表格工具行,作为价格视角(按量 / 竞价 / 包周期)并透传创建页。
- **「选择规格」卡 = `SkuPicker` full 变体**(与部署页 compact 变体同一份表格、库存口径与灰置规则):
  - 顶部 `GPU 算力 / CPU 算力` Segmented,切换即清空已选行并重置筛选。
  - **GPU 栏**筛选 chips:`GPU 型号`(每项只带一个数字「可开 N」)|`档位`(专用整卡 / 共享·标准 / 共享·经济);`显存` 折进「更多筛选」二级行;首位「全部」。**每个 chip 带 facet 计数**只在「共 N 个规格」行体现,0 结果的 chip 灰置(`GatedButton`)+ 原因「当前其他筛选下没有匹配的规格」,不隐藏。
  - **购买数量**(`GPU 数量` 1/2/4/8)是独立 chip 行,**不是筛选**:改变不清空已选行、不进「清除筛选」;所选行库存不足该数量时保持选中但 CTA 灰置(`GatedButton`「该卡数当前空闲库存不足」)并在行内标「不足 N 卡」。
  - **CPU 栏**只有 `vCPU` 与 `内存` 两行 chips(取值域从本栏 SKU 聚合,首位「全部」)。
  - 表格上方工具行:「共 N 个规格 · 清除筛选」(有筛选时才出清除)+ 计费方式 chips。
  - 筛选、选中、数量、计费方式全部入 URL(`MarketSearch`:`kind / model / tier / vram / vcpu / mem / sku / qty / mode / count`,默认值剥离)。
- **SKU 表格(radio 单选,固定规格名列与价格列)**,列:选择 | 规格名 + 档位徽标(标准档 hint「显存与算力硬隔离」、经济档 hint「性能可能波动」、CPU 档 hint「不带 GPU」)| GPU 型号/显存(标准档写 MIG 切片名,经济档写份额与算力 %;CPU 档表头「CPU / 内存」)| 可开实例(≥ 所选卡数 绿数字 / 不足写「不足 N 卡」橙标 / =0「已租完」)| 实例配置 vCPU·内存 | 实例盘 | 最高 CUDA(CPU 档「-」)| 价格(钉右列)—— **表头「单卡 ¥/时」(GPU 栏)/「整机 ¥/时」(CPU 栏)**;所选卡数 >1 时价格格内副行 `× N 卡 = ¥Y/时`;竞价视角下价格显示折后价 + 原价划线。
  **不可选行**(库存为 0 / 竞价档未上竞价):radio 禁用 + 弱化底色(`.sku-row--disabled`)+ radio 处 tooltip 原因,**排到末尾**,不隐藏。
- 选中「共享·经济」规格时,卡内就地出一行风险摘要(完整条款在创建页提交前的知情同意里)。
- **计费方式 chips**:`按量计费`(默认)|`竞价` 低至 X 折 |`包日` -5% |`包周` -10% |`包月` -20% |`包年` -30%,折扣角标从 `/policies` 读;选中规格 `period_enabled=false` 时四个周期项灰置、`spot_enabled=false` 时竞价项灰置(`GatedButton`「规格不支持」)。选中周期后出「购买时长」(1~36)。**选中的规格不支持当前计费方式时**:`message.info`「该规格不支持包月,已切回按量计费」并把 URL 里的 mode 清掉,不让 chip 静默跳动。
- **底部通栏结算条(sticky,与创建页共用 `CheckoutBar`)**:规格汇总 + 费用大字(按量 / 竞价:`配置费用 ¥X.XX/时` 后缀「× N 卡」或「整机」;包周期:周期总价,原价划线 + 折后)+「费用明细」Popover(标明以创建页最终报价为准)+ 唯一主按钮:未登录「登录后租用」(未选规格同样灰置 + 原因;跳登录带回完整筛选态)/ 已登录「配置实例」。市场只建开发机,不放「部署服务」入口。
- 未登录可浏览;`POLL.steady` 轮询 + 页头新鲜度;空态「没有符合条件的规格」+ 「清除筛选」。

### 3.5 屏 ④ 创建实例

全页 `SectionRail` 骨架(与部署页 §3.6 同一组件),只建开发机。承接查询参数:`?gpus=N`(1~8)、`?period=day|week|month|year`、`?market=spot`、`?count=`;**`period` 与 `market=spot` 互斥,同时带以 `period` 为准**。页头「← 返回算力市场」是**唯一返回入口**(带回市场筛选态;脏表单走离开确认),结算条不放「取消」。

```
├────────────┬─────────────────────────────────────────────────────────┤
│ ✓ 基本信息 │ 基本信息                                    更换规格 → │
│ ✓ 计费方式 │  H100 ×1 · 8 vCPU · 32G · 实例盘 100G · 标准 · ¥2.50/时 │
│ ● 镜像     │  实例名称 [不填则自动生成      ]  GPU 数量 [1卡][2卡][4卡⃠]│
│   选择镜像 ├─────────────────────────────────────────────────────────┤
│ ○ 数据盘   │ 计费方式 [按量计费][竞价 低至4折][包日][包周][包月][包年]  │
│ ○ SSH 密钥 ├─────────────────────────────────────────────────────────┤
│ (sticky)   │ * 镜像   平台镜像 | 自定义镜像                            │
│            │  ┌✓PyTorch 2.13┐┌TensorFlow┐┌Miniconda┐┌Paddle┐        │
│            │ 数据盘(可选)  (●不需要)(○新建数据盘)(○挂载已有盘)         │
│            │ * SSH 密钥   ☑ laptop-ed25519 (SHA256:…)    添加公钥      │
├────────────┴─────────────────────────────────────────────────────────┤
│ 还差 1 项:选择要挂载的数据盘                                          │
│ [H100 ×1 · 8 vCPU · 32G]  配置费用 ¥2.50/时 ×1卡  费用明细  余额 ¥3,560│
│                                                     [  创建并开机  ]  │
```

**目标是一键创建**:从市场页落到本页,无需再输入任何必填项即可点主按钮 —— 镜像默认推荐项、只有一把公钥时自动选中、名称与数据盘可选。左侧 rail(≥md 竖向 sticky,<md 顶部横向)段状态由 `deriveSectionStatus` 派生:无问题且已触碰 = `finish`,无问题未触碰 = `wait`,第一个有问题的段 = `process`,**其它有问题的段只在被触碰或点过提交后才标 `error`**(首屏不出红叉)。卡片顺序:

1. `基本信息`:已选规格**一行摘要**(`H100 × 1 · 8 vCPU · 32G 内存 · 实例盘 100G · 档位标 · ¥X/时`)+ 右上「更换规格」(→ 市场并带回筛选态)+ 实例名称(可选,默认生成)+ `GPU 数量` chip(1/2/4/8,受 SKU 上限与库存约束,不足档位 `GatedButton`;CPU 规格不出此 chip,提交 `gpu_count: 0`)。
2. `计费方式`:`BillingModeCard`(chips 同 §3.4)。选了周期:结算条主数字换「包月费用 ¥X / 月」,**「原价 / 优惠 / 应付」三行直接摊在结算条第二行**,「到期时间 约 YYYY-MM-DD」降级为正文字号,主按钮**「支付并创建」**,提交 `market/period/period_count`。选了竞价:结算条主数字是折后时价(原价划线 + 折扣角标),提交体 `market='spot'`、不带 `period`。URL 带来的计费方式对该规格不可用时静默回按量(chip 已灰置并注原因)。
3. `* 镜像`(必填,标题红星):Tab **平台镜像**(**常用镜像 `OptionTile` 网格** ≤4 个,每框架取清单首条,按 PyTorch / TensorFlow / Paddle / Miniconda / DataScience 排序,默认选中第一个;tile = 框架图标 + 「框架 版本」+ 副行「CUDA X · Py Y · 已预热 / 未预热」;「更多镜像…」展开四层级联 框架 → 版本 → Python → CUDA;选定后回显完整 `image_ref`(`CopyField`)与预热状态;CPU 规格只列不带 CUDA 的镜像且不承诺秒级启动)/ **自定义镜像**(仓库地址一栏,须钉版本,`:latest` 与无 tag 即时红框 + 原因;提示只写一行「镜像须内置 SSH 22 与 JupyterLab 8888」,其余说明进 `?`)。**切 Tab 记忆各自的选择**;切回平台镜像且为空时回填推荐项,当前生效的镜像只有一个来源。
4. `数据盘(可选)`:`OptionTile` 三项「不需要 / 新建数据盘 / 挂载已有盘」(副行写口径与可挂载盘数);「新建」行内直建:`DiskSizeField`(滑块 + 数字框,初值取 `policies.disk_min_gb`),盘名称折在「高级」里;一句摘要「N GB · ¥X/GB·月,约 ¥Y/日;提交时自动创建并随实例挂载」。卡脚注:数据盘独立于实例、关机也计费 · 实例盘为节点本地盘不做冗余。
5. `* SSH 密钥`(必填):多选已有公钥;只有一把时自动勾选;无密钥时行内添加(名称 + **多行公钥框**)并自动选中,给 `ssh-keygen -t ed25519` 命令 + 复制与「公钥在 ~/.ssh/id_ed25519.pub」指引(不支持密码登录)。

**结算条**(`CheckoutBar`):

- 条上方 `notice` 槽:**未完成项清单**「还差 N 项:选择镜像 · 选择或添加 SSH 公钥 · 选择要挂载的数据盘」,每项可点击滚到对应段(`scrollToSection`);`NO_CAPACITY` 也常驻在此槽(「当前规格空闲 GPU 不足」+ 「换个规格」按钮),不用 toast;主按钮灰置只作为兜底。
- 费用项:按量 / 竞价 = `配置费用`(大字 + 「× N 卡」/「整机」后缀)+ `数据盘费用(按日)`(**只在真挂了盘时出**);包周期 = 周期费用 + 数据盘费用(如有)+ 到期时间(降级);「费用明细」Popover 逐行摊开(单价 × 卡数,CPU 规格写「整机 ¥X.XX」;盘价 GB·月折日;注明计费依据为实例事件流水)。
- 主按钮文案:按量「创建并开机」、包周期「支付并创建」;提交中分步文案「正在创建数据盘…」→「正在创建实例…」;余额不足时按钮变「余额不足,去充值」;余额查询失败时 `GatedButton` + 可重试错误条。
- **<sm 折叠为一行**:价格 + 主按钮常驻,明细 / 余额 / 未完成项进「明细 ▴」底部 sheet。
- **知情同意只弹一个**(`ConsentGate`,§1 规则 6):竞价一节(五条)/ 共享·经济一节(四条),命中几节出几节,一次勾选「我已阅读并知悉以上事项」,确认文案「我已知悉,继续创建」;创建失败再提交时勾选态保留。
- 创建带 `Idempotency-Key`(参数快照派生,失败不轮换);**建盘成功而建实例失败**:条上方常驻 `Alert`「数据盘已创建并开始计费…再次提交将直接挂载它,不重复建盘」+「去存储页」。
- 成功后 message「实例 {name} 创建中」并**跳到该实例详情的「连接」Tab**(`/instances/:uuid?tab=access`)。

### 3.6 屏 ⑤ 部署服务

`/services/new`(入口:「在线服务」页头主按钮与空态按钮、命令面板「部署服务」;规格在本页 ① 段紧凑选择器里选。深链 `?sku_id=&gpus=&period=|market=spot&count=` 预填,已选规格折叠成一行回显 + 「更换规格」)。页头「← 返回在线服务」(脏表单走离开确认)。

与创建实例同一 `SectionRail` 骨架 + 底部结算条:全部受控 state + 派生问题,不用 antd `Form.validateFields`;**每段独立标状态**(规则同 §3.5:首屏无红叉),点击滚到该段。**字段级错误就地显示**(端口 / 健康检查在 blur 后红框 + 红字),结算条上方给「还差 N 项」可点击清单。

1. `基本信息`:`服务名称`(可空,自动生成)/ `算力规格`(`SkuPicker` compact:GPU / CPU 分栏 + 型号 / 档位 chips + SKU 表 radio;带 `?sku_id` 进来时折叠成一行回显 + `更换规格`;GPU 数量 chip 受库存约束)/ `计费方式`(与 §3.4 同 chips;竞价只警示不禁止:卡下一行「竞价实例可被回收,不建议用于对外服务」)。
2. `容器配置`:镜像地址(**必须钉版本**,`:latest` 与无 tag 即时红框;一行规则 + `?` 详情)/ 启动命令 / 启动参数(`RowsEditor`:图标删除钮、行分隔、批量粘贴带预览)/ 环境变量(名 + 值(密文行用密码框)+ 密文勾选 + 批量粘贴带预览与跳过计数)/ **数据盘(可选)归在本段**(与创建实例同组件的 section 变体)。
3. `服务配置`:服务端口(∉ {22, 8888})/ 协议(`OptionTile`:HTTP;TCP、gRPC 灰置「即将上线」)/ 健康检查路径(以 `/` 开头)/ 访问鉴权(`OptionTile`:需要 API Key / 公开访问)/ 服务端点「部署后生成」。
4. `高级配置`:`☐ 同时开放 SSH`(勾选内联公钥多选,≥1,问题就地显示)/ 更新策略只读说明「重建更新:先停旧版本再起新版本,期间端点 503,地址与 Key 不变」/ 配置摘要(`KeyValue`)。

结算条与创建实例同构:主按钮「部署服务」(包周期「支付并部署」),余额不足变「余额不足,去充值」;知情同意走 `ConsentGate`(确认文案「我已知悉,继续部署」);`Idempotency-Key` 按参数快照派生,失败不轮换;成功后 message「服务 {name} 部署中」并跳 `/services/:slug`。脏表单离开走确认。

### 3.7 屏 ⑥ 在线服务列表

- **页头**(`PageContainer`):标题 + 标题旁 `?` tooltip(停机 / 冻结策略:「停止的服务端点返回 503,不再产生 GPU 时费;欠费冻结 N 小时后回收实例盘,数据盘不受影响」,N 取 `policies.freeze_grace_hours`)+ 右侧「刷新(新鲜度)/ `部署服务`(唯一 primary)→ `/services/new`」;`FilterBar`:状态 Select + 搜索框(`?status=` 与 `?q=` 入 URL,replace)+ 清除筛选 + 共 N 条。
- 表格 8 列(固定名称列 + 操作列,sticky):①`名称 / ID`(名称链到详情 + slug `Mono`)②`状态`(`serviceStatusMap` 徽标;`unready` 为 warning 并带解释 tooltip,冻结附回收倒计时)③`服务端点`(主机名 `CopyField` 复制完整 URL;第二行 running / unready 写「就绪 ✓/✗ · 需要 API Key / 公开访问」,其余「已停止,端点暂不可达」)④`规格`(取当前版本实例)⑤`版本`(`v{no}`)⑥`费用`(与实例列表同口径)⑦`创建时间` ⑧`操作`(`RowActions`:可启动(stopped / failed)主动作 `启动`,其余主动作 `端点 ▾`(primary + 链接图标,与实例「连接 ▾」同一外观;条目 复制访问地址 / 打开端点 / 调用示例,服务不在 running / unready 时条目灰置带原因「服务未运行,端点暂时打不通」);次动作 running / unready 为 `停止`,冻结与过渡态为灰置的 `启动` 带原因,可启动时无次动作;更多 ▾ 里 `访问密钥` / `设置` 直达详情对应 Tab 与 `删除服务`)。**不做双击行**。
- 列表不轮询;`deploying / stopping / releasing` 逐条 `POLL.transient` 轻轮询。`unready` 不算过渡态。
- 空态 `EmptyState`「还没有在线服务」+ `部署服务`;筛选无结果「没有匹配的服务」+ 清除筛选;查询失败错误态优先。
- 停止 = 二次确认(端点 503、实例关机停止 GPU 计费、端点与 API Key 保留;包周期加「周期内停止不退费,库存为你保留」);删除 = 键入服务名 + 勾选「服务端点将立即失效,API Key 不可恢复」,运行中不能删(先停)。

### 3.8 屏 ⑦ 服务详情

`EntityHeader`(面包屑带回列表筛选态):名称(行内改名)/ 状态徽标 / 档位 / `v{no}` / 包周期与竞价标 + 元信息条(ID = slug `Mono` + 复制 / 规格 / 计费 / 今日消费或到期时间 / 创建时间)+ 操作组(middle;主动作同列表)。
**服务端点卡常驻在 Tab 之上**:完整 URL(`CopyField code`)+ 打开;副行「就绪 ✓/✗ · 需要 API Key / 公开访问 · 容器端口 X · 健康检查 Y」;状态提示**四态分开**:`deploying`「正在部署 v{no},实例就绪前端点返回 503」;`unready`「服务还没就绪,实例仍在运行、照常计费」+「看日志」;`stopped / stopping`「服务已停止,端点暂时打不通;启动后恢复」;`frozen`(error)「因欠费冻结…充值后自动解冻」+「去充值」;`failed`(error)「新版本启动失败…」+「看日志」「查看事件」。**就绪为 ✗ 不报故障。**

头部操作组多一个「更新版本」:`deploying / stopping / releasing / released` 灰置(`GatedButton`「部署完成后才能更新版本」),包周期灰置「包周期服务暂不支持更新版本」。

Tab 固定 `概览 / 访问密钥 / 监控 / 日志 / 历史 / 设置`(白名单 `SERVICE_DETAIL_TABS`,`?tab=` 直达,切换 replace,非法值回默认;公开访问时不出「访问密钥」Tab,其深链落到「设置」):

- **概览**(默认):`当前版本 v{no}` 只读回显(`KeyValue`:镜像 / 容器端口 / 健康检查 / 访问鉴权 / SSH / 启动命令 / 启动参数 / 环境变量:明文项显示值,密文项只显示键名)+ 尾注「参数随版本固定,创建后不可修改」;`调用示例` curl(URL 用端点根;Key 用未吊销 Key 前缀 + 省略号);**小时账单**(全部版本实例)在本 Tab 下方。
- **访问密钥**(仅 `require_api_key` 时出):表(名称 / Key 前缀 `Mono` / 最近使用 / 创建时间 / 吊销)+「新建 Key」(成功态一次性展示:`CopyField secret` 全值 + 「关闭后无法再查看」,**关闭前必须勾选「我已保存」**);已删除的服务禁止新建。
- **监控** / **日志**:打当前版本实例(面板同 §3.3);日志在 `deploying / running / unready` 可读。
- **历史**:上半「版本」= 全部版本实例(含已释放)降序表(`v{no}` + 当前标 / 状态 / 镜像 / 实例(链到实例详情)/ 创建时间);下半「事件」= 全部版本实例事件并集(面板同 §3.3);版本更新的两条 reason 是「版本更新:旧版本关机」「版本更新:旧版本释放」。
- **设置**:`访问鉴权` Switch(PATCH `require_api_key`;关 → L2 确认「任何人拿到端点都能调用,照常计费;已有 Key 保留」;开着但没有未吊销 Key → warning「当前没有可用的 API Key」+「去新建 Key」;副文案「保存后几秒内生效,不重新部署」)· `调试 SSH`(`with_ssh` 且运行中出连接串 `CopyField`;未开放写「SSH 开关随版本固定」)· 危险区删除服务。已删除的服务三处控件灰置。改名在头部完成。

**「更新版本」= Drawer**(`RevisionDrawer`,`drawerWidth.lg`;提交与取消固定在 `footer`;脏表单点遮罩 / 关闭 / 路由跳走都走离开确认;提交后头部当场翻成 deploying;抽屉态不入 URL):
顶部警示「重建更新:先停止旧版本,新版本就绪前端点返回 503;服务端点与 API Key 不变;新版本启动失败时旧版本保留(停机),可「启动」回滚」;
`容器配置`(按当前版本预填;环境变量默认折叠为「N 项 · 展开编辑」;**密文键单独一栏**,每个默认「沿用当前值」(键名进 `env_secret_keep`),可「覆盖为新值」或「删除」;当前版本挂了数据盘时提示新版本不挂盘)· `服务配置`(端口 / 健康检查;鉴权注明「在「设置」里改」)· `高级配置`(☐ 同时开放 SSH → 公钥多选)· `规格与计费` 只读「沿用 v{no}:规格 · 计费;要换规格请新建服务」。提交前 L2 确认;幂等键 `idemKeyOf("svc-rev", [nonce, slug, revision, 表单快照])`;成功 message「新版本 v{n} 部署中」并关抽屉。

「更多 ▾」两处(列表 / 详情)一致:访问密钥(→ `?tab=keys`)/ 设置(→ `?tab=settings`)/ ─ / 删除服务(红)。只有一条服务轮询(过渡态 `POLL.transient`、运行中 `POLL.steady`、已删除停)+ 页头新鲜度,端点卡与头部同源。

### 3.9 屏 ⑧ 费用中心

```
┌ 费用中心                                                             ┐
│ ⚠ 充值前需完成实名认证                                     [去认证]  │
│ ┌可用余额──────────────┐ ┌2026-09 消费概览            [2026-09 ▾]─┐ │
│ │ ¥3,560.00     [充值] │ │ GPU 时费 ¥1,505.50 │ 数据盘 ¥13.30 │ 今日 ¥41.70│
│ │ 预警阈值 24 小时·修改│ │ 按实例  train-llama ████████████ ¥1,440.00 │
│ └──────────────────────┘ └───────────────────────────────────────────┘ │
│ ┌ 小时账单 | 收支明细 | 退款 | 发票                     [导出 CSV] ┐ │
```

- `PageContainer`;余额 / 月度 / 今日三路查询失败与实名横幅经 `AttentionBar` 聚合为一条。
- 余额卡 = `StatCard`(`可用余额` 大数字 + `充值` 主按钮 middle)+ 脚注一行「预警阈值 N 小时 · 修改」(→ `/settings?tab=notify`);阈值设置只在账户设置页。
- 充值 modal:渠道 `OptionTile`(由平台配置驱动,未开通的 tile 带原因「商户资质接入后开放」,开发环境可用「模拟支付」且默认选中);**金额只有一个控件**(档位 chip 写入数字框)+ 「充值后余额 ≈ ¥X」预览 → 二维码(antd QRCode)+ `expires_at` 倒计时(`formatCountdown`)+ 2s 轮询自动确认(终态即停);二维码态可「修改金额」回到上一步;倒计时归零显示「订单已过期」。
- 消费概览卡:三张同尺寸 `StatCard`(本月 GPU 时费 / 数据盘费用 / 今日消费)+ 「按实例」横向条(可点进实例详情账单 Tab),不用饼图。
- 账单区四个 Tab:`小时账单` | `收支明细`(`CursorTable`)| `退款` | `发票`;`导出 CSV` 与 Tab 同一行右侧,只在前两个 Tab 出现,按当前 Tab 口径走 `GET /api/v1/billing/export`,文件末尾出现截断标记行时页面提示已截断;脚注「日常费用…」只在小时账单 Tab 下出现。

### 3.10 屏 ⑨ 存储

- `PageContainer`(主按钮「新建数据盘」→ modal:名称(可选,自动生成)+ `DiskSizeField`(初值取策略下限),实时折日估算)。
- 顶部「挂载全景图」横条:`/root`(实例盘·随实例回收·免费,容量随规格,不写死数字)· `/root/data`(数据盘·¥X/GB·月·独立保留)。**只列真实挂载点**。
- 数据盘列表:名称 / 容量(右对齐)/ 计费(每盘快照价 `price_gb_month`,折日小字)/ 状态 / 到期·回收(active「按日扣费中」;grace「宽限期(只读)剩 X 天」;frozen「冻结中,X 天后清除」;deadline 由 `grace_started_at`/`frozen_started_at` + `/policies` 天数前端计算)/ 挂载中的实例 / 创建时间 / 操作(`RowActions`:`扩容` 抽屉(`DiskSizeField` 带基线与差价,footer 取消 + 确认)、`删除` 两道闸(键入盘名 + 勾选))。
- 无「续费」按钮。回收策略透明:欠费 → 7 天宽限(只读)→ 冻结 30 天 → 清除,列表行倒计时。空态 `EmptyState(disk)`「数据盘独立于实例,释放实例不丢数据」+ 新建。

### 3.11 屏 ⑩ 账户设置

`PageContainer width="narrow"`,四 Tab(`?tab=`,白名单 `ssh / notify / realname / account`,默认 `ssh`):

- **SSH 公钥**:表(名称 / 指纹 `Mono` / 添加时间 / 删除(L1))+ 添加表单(名称 + 多行公钥框 + `添加公钥`);`/settings#ssh` 深链落本 Tab 并高亮。
- **通知**:低余额预警阈值(小时)+ 保存;副文案「预计可用时长低于该值时短信 + 站内信提醒」。
- **实名认证**:四态(未就绪骨架 / 错误可重试 / 已认证 / 未认证);未认证时竖向表单(姓名 / 身份证号 / 提交核验);平台未开通实名时表单可见但 `GatedButton` + 说明。
- **账号**:手机号 · `设置 / 修改密码`(modal,凭手机号 + 验证码)· `退出登录`(普通按钮,L0)· `登出全部设备`(L2,非红)· 页尾 `DangerZone`「注销账号」(L3:键入手机号 + 原因必填;冷静期倒计时 + 撤销;驳回原因回显)。

### 3.12 屏 ⑪ 支持与工单

- `PageContainer width="narrow"`,主按钮「新建工单」;正文:自助排查 FAQ 链接 + 联系客服卡 + 我的工单列表(状态 Segmented 筛选入 `?status=`;行 = 工单号 `Mono` + 标题链接 + 状态标 + 更新时间;不整行点击)。
- `/support/:ticketId`:`PageContainer back`(带回列表筛选态)+ 元信息(`KeyValue`)+ 对话流(`TicketBubble`,贴底,Ctrl/⌘+Enter 发送);发送按钮不足 2 字时 `GatedButton`「至少 2 个字」;`关闭工单` 常驻,非 resolved 时 `GatedButton`「工单解决后才能关闭」。

### 3.13 通知中心

`PageContainer width="narrow"`,标题旁「全部已读」;全部 / 未读 Segmented(`?filter=`);行点击已读并跳转(跳转优先结构化 `target_id` 精确深链);空态 `EmptyState(notification)`。

### 3.14 状态与文案体系(两端共用,收进 `packages/ui`)

- **状态枚举 → 徽标色 / antd Badge 语义 / 图标 / 文案 key 的单一映射表**(`status.ts`:三种显式形状 `LabelMeta`(只有文案)/ `ColorMeta`(+ 颜色)/ `StatusMeta`(+ badge 语义 + 可选 `icon`);实例 / 服务 / 订阅 / 镜像缓存 / 节点注册 / **节点状态** / **告警严重度** / 订单 / 调账 / 法务 / 退款 / 发票 / 工单 / 注销 / 数据盘 / 公告 / 购买模式 / 周期 / 事件原因;`ALL_STATUS_MAPS` 收口,`locales.test.ts` 遍历它保证键齐全)。页面禁止裸输出状态码:两端一律经 `StatusTag`(`map` + `value` + `variant`),web 的 `InstanceStatusBadge` / `ServiceStatusBadge` / `TierTag` 只是 ≤5 行包装;`hintKey` 自动出 tooltip(两端一致);未知值原样回显灰标,不进 `t()`。
- **格式化只有一份**(`format.ts`,经 `useFormat` 拿到带 locale 的版本):金额 `¥1,234.56`(`formatMoney`,BigInt 万分位,禁浮点)、时价 `¥X.XX/时`(`formatHourlyPrice`,单价 × 份数走 `mulPrice`)、周期价 `¥X/月` 与 `¥X/3 月`(`formatPeriodPrice`)、时长 `X 小时 Y 分`、倒计时 `剩 Xh` / `剩 X 天` / `X 后回收`(`formatCountdown` / `formatDaysLeft` / `formatReclaimCountdown`)、容量 `formatSizeGb`、时间 `formatDateTime`(带时区后缀;相对时间只许 `Tooltip` 里带绝对时间)。查询未就绪或空值一律 `EmptyValue`(「—」),不显假 `¥0.00`、不混用「-」。
- **文案分层**:后端 `core/messages.py` 是错误与状态文案事实源(经 `errors` namespace 同步到两端,组件里用 `useTranslation("errors")` 直接引用);两端页面文案在各自 `locales/*/web.json` / `admin.json`;共享状态 / 格式 / 通用件文案在 `packages/ui/locales/*/shared.json`(`common.*` / `filter.*` / `attention.*` / `leave.*` / `freshness.*` / `confirm.*` / `empty.*` / `status.*`)。namespace 划分与闸门见 [`reference/i18n.md`](./reference/i18n.md);GPU 公开规格静态表 `gpuSpecs.ts`。管理端平台配置的字段标签、策略参数名与风险复述同样进 locales(中国渠道运营域的专有名词按 `i18n-exempt` 标记不译)。
- **确认文案 = 标题问句(含目标)+ 后果正文**(`useConfirm` 的 `title` / `consequences`);高危双步(`ReasonAction`)两步都回显目标,第二步再回显原因;知情同意条目与后端硬规矩同源(§1 规则 6)。
- **禁词与语气**见 [`copy-style-guide.md`](./copy-style-guide.md):按钮动宾 ≤6 字、空态一句话 + 一个动作、禁用 tooltip 只写前置条件、不用「您」/ 感叹号 / emoji;CI 强制禁词表(`scripts/check-copy-banned.sh`)。

## 4. 管理端

### 4.1 信息架构与角色

导航分四组(事实源 `apps/admin/src/lib/menu.ts` 的 `MENU` + `group`,侧栏与命令面板共用;可见性由 `MENU_ROLES` 按角色过滤;**页面标题必须等于导航标签**):

```
总览   运营总览 `/`
资源   节点与 GPU `/nodes` · 集群 `/cluster` · SKU 与定价 `/skus` · 镜像与预热 `/images`
业务   租户与实例 `/tenants` · 在线服务 `/services` · 财务对账 `/finance` · 工单 `/tickets`
治理   告警中心 `/alerts` · 审计日志 `/audit` · 平台配置 `/platform` · 系统设置 `/settings`
```

侧栏组标题只在展开态渲染;桌面可手动收成 80px 图标轨(`localStorage("superdl.adminSider")`),收起态点图标由 `Menu.onClick` 导航;窄屏导航走带遮罩的 Drawer(Esc 关闭);`/alerts` 对 finance 不可见。顶栏 sticky(`layout.topBarHeight`),左环境徽标(`useEnvironment()`,prod 另加 3px 红色上边线),右 ⌘K 触发器 · 语言(图标下拉)· 告警铃(Popover 前 20 条 + 确认 + 底部「查看全部」「全部确认」)· 用户名与角色 ▾ 退出;md 以下三者收进用户下拉。壳带 skip-link 与 `<main id="main">`。

**页面骨架**:`PageContainer`(宽表页 `width="full"`:节点 / SKU / 租户与实例 / 财务 / 在线服务 / 镜像)+ `PageHeader`(标题 · 右侧动作 · 轮询页新鲜度条:总览 / 节点 / 镜像 / 告警);首屏 KPI 用 `KpiGrid` + `StatCard`(逐卡骨架,各卡只等自己的 query,**每张可点击深链**)。

**命令面板(Cmd+K / Ctrl+K)**:与 web 端同范式;页面按侧栏分组分节 + 实体检索(租户 id / 实例 uuid 前缀 / **节点名 / SKU 名 / 服务名**)+ 快捷动作(未确认告警深链 `/alerts?acked=unacked`、刷新当前页数据);双语关键词;选中行底色走 `base.css`。

角色:admin(全部;SKU 增改与改价、策略参数写入仅 admin)/ ops(资源 + 实例;SKU 与策略只读)/ finance(财务区可写:调账发起与复核、订单核验与补单、退款审批与打款登记、发票开具与驳回、结算缺口重放与核销,其余只读)/ readonly(全站只读)。逐端点角色见 [`reference/admin.md`](./reference/admin.md)。

高危与不可逆操作统一走 `ReasonAction`(原因必填 → 二次确认 → 审计):强制停止、强制回收、冻结租户、封锁节点、吊销注册令牌、重新生成注册命令、SKU 下架、死信重放与忽略、删除镜像、撤回公告、注销申请驳回、退款与发票驳回、结算缺口核销、管理员停用与重置两步验证。**恢复方向动作(解封节点、解冻租户、上架 SKU)只填原因,不做第二步确认。** 账号注销执行与**节点退役**走 L3(`TypeConfirmModal`:键入目标名 + 勾选 + 必填原因);节点退役的确认框须写明两条平台管不到的边界(不吊销 kubelet 证书、join token 轮换交回运维),节点上有未释放实例时确认按钮文案换成「强制退役」并前置红色告警。SKU 改价与强制上架走 `useConfirm`(变更行 + 影响面 + 范围说明;影响面查询在途时确认禁用)。调账另加双人复核,发起人不能自审。

### 4.2 逐屏要点

通用骨架:`PageContainer`(宽表页 `width="full"`)+ `PageHeader`(标题 · 右侧动作 · 轮询页新鲜度条);首屏 KPI `KpiGrid` + `StatCard`;宽表固定标识列 + 操作列 + sticky 表头,数值右对齐,标识 `Mono`;行内 `RowActions`(≤1 主 + 1 次 + `RowMoreMenu`);多选行出 `BulkBar`;**筛选一律 `FilterBar`**(清除筛选 + 服务端 total)并入 URL;检索框 ↔ URL 走 `useUrlCommittedInput`;截断表挂 `ListCapNote`;游标分页表走 `CursorTable`;状态标 `StatusTag`;空态 `EmptyState`,错误 / 403 走 `TableErrorEmpty`(抽屉内 `compact`),取数失败条走 `DataErrorAlert`;编辑抽屉 footer 提交 + 取消 + `useLeaveGuard`,宽度只取两档;图表三态(loading / empty / degraded)在 `EChart` 内。

- **运营总览**:

```
│ 运营总览   8 秒前更新 · 每 30 秒自动刷新 ⏸ ⟳                          │
│ [✕2 未确认严重告警][⚠1 失联节点][⚠3 死信任务][ⓘ0 结算缺口][ⓘ4 待审批]│
│ ┌今日收入 ↗┐┌本月收入 ↗┐┌今日新注册 ↗┐┌付费租户 ↗┐   ← 每张可点,深链 │
│ ┌活跃实例 ↗┐┌包周期在保↗┐┌节点健康 ↗┐┌未确认告警 ↗┐                 │
│ ┌实际超卖率(按池)? ────────────┐ ┌实时告警流  [级别▾] 8 秒前 ┐   │
│ ├真实利用率 24h(按池)──────────┤ │ ✕ 严重 … [确认]              │   │
│ ├死信任务 ● 3 条待处理(默认展开)┤ └─────────────────────────────┘   │
```

`TriageBar` 置顶(未确认 critical 告警 → `/alerts?severity=critical&acked=unacked` · 失联节点 → `/nodes?status=Missing` · 死信任务 → 本页锚点 · 结算缺口 → `/finance?tab=gaps` · 待审批(退款 / 发票 / 注销)→ 对应 Tab;0 计数弱化不隐藏)。KPI **两行**——资金与租户(今日收入含昨日对照 / 本月收入 / 今日新注册环比 / 付费租户,分母进副行)、运行与风险(活跃实例 → 实例 Tab / 有效订阅 / 节点健康 → 节点 / **未确认告警** → 告警中心),各卡只等自己的 query;主图表拆两张单轴:「实际超卖率(按池)」与「真实利用率 24h(按池)」(60% / 85% 阈值线,规则说明进 `?`);GPU 池占用堆叠条(按节点池分组,已租 / 空闲,已租段内分出「其中竞价(可回收)」);右栏实时告警流(`POLL.steady` + 新鲜度;severity 筛选入 URL;条目 = 严重度图标 + 文字 + 标题;查询失败如实显示错误态;条目按 `target_kind` 深链:`tenant` → `/tenants?tenant=<id>` 直开抽屉、`node` → `/nodes?node=<名称>`、`ticket` → `/tickets?id=<id>`);有死信时任务死信卡**默认展开**(单条 / **勾选批量**重放 · 忽略,一条原因作用于全部所选)。

- **节点与 GPU**(全宽;页头新鲜度条,`POLL.steady` 可暂停):待入网节点卡(池 / 主机名 / 备注 / 状态 / 阶段 / 心跳 / 错误 + 重新生成加入命令 / 吊销,确认文案带主机名)与「添加节点」生成一次性加入命令;`FilterBar`(名称 / 池 / 状态,入 URL);节点表(名称(`Mono`,固定左)/ **状态**(紧跟名称,`nodeStatusMap`:就绪 / 未就绪 / 已封锁 / 失联,带图标与 hint —— 放表尾会被横向滚动挡住)/ 池标签(切池在途显示「旧 → 新」)/ GPU 型号×数量 / 显存 / **已用(GPU 卡当量)** / **实例(未释放实例数,含已关机;链到 `/tenants?tab=instances&inode=<节点>`)** / 驱动 / CUDA / vCPU / 内存 / 磁盘(数值右对齐)/ 最近心跳(相对时间 + tooltip 绝对时间)/ 操作(固定右)`封锁`·`解封`(动词与状态同词根,不叫「停止调度」;经 outbox;解封只填原因;封锁可逆,触发钮不标红,红色留给确认框)· 次动作 `切换池`(`-SwitchPoolModal`:当前池只读 + 目标池 Select(排当前池与 cpu,机型不支持 MIG 的 `mig` 灰置)+ 原因,`useConfirm` 二次确认;切池不需要节点侧动作,提交即受理、无回执命令;节点上有未释放实例时触发钮灰置并说明条数)· 更多 ▾(`drain` 占位「经集群 Runbook 执行」、`退役` 标 danger);**勾选多节点批量封锁 / 解封**);点节点行(整行可键盘选中)→ **右侧 Drawer(`drawerWidth.lg`,`?node=` 入 URL 可直链)**:`EntityHeader`(名称 / 状态 / 池 / GPU / 驱动 / CUDA / 心跳 + 操作)+ 每卡热力网格 + 节点级 ECharts 曲线(1h/6h/24h,24h XID 计数红标),配了 `grafana_url` 才多一个外链按钮。子文件:`-GpuGrid` / `-NodeMetricsPanel` / `-AddNodeModal` / `-SwitchPoolModal` / `-EnrollmentsCard` / `-NodeDrawer`。
- **集群**(全宽):横幅(取数失败 / 集群不可达 / 轻量集群 / 未打池标签 / 监控未接入)经 `AttentionBar` 聚合为一条。**组件体检整幅铺开,一项一个小面板**(`Col xs=24 sm=12 lg=8 xxl=6`):状态点(`componentHealthMap` 五态,图标 + 文字,不只靠颜色)+ 组件名 + 主数字(`fontSize.kpi`,`tabular-nums`,色随状态)+ 两条事实(label 灰、值 `Mono`,`tone` 决定着色);**面板正面只放可核对的数字与标识符,不放形容词**;按 `故障 → 降级 → 未启用 → 未知 → 正常` 排序,标题带待处理计数,故障态面板内联 `fix_hint`。点面板 → **`/cluster/$component` 子路由渲染成右侧 Drawer**(`drawerWidth.lg`,遮罩在集群页上,可直链、可转达、后退即关;页容器由父路由自持,该文件在 `scripts/check-page-skeleton.py` 有显式豁免):`EntityHeader size="drawer"`(组件名 + 状态徽标 + 探测时间)+ 判据 → 事实(`KeyValue`,值可复制)→ 对象明细(`Table size="small"`,列由 `-componentMeta` 定)→ 影响面(仅非正常态)→ 下一步(`diag_hint` 排障命令 + `fix_hint` 修复命令,`CopyField code block`)。池分布标签显示「就绪/总数」,可售性只看就绪数。子文件:`-ComponentPanel` / `-ComponentDrawer` / `-componentMeta`。
- **SKU 与定价**(全宽):`FilterBar`(型号 / 档位 / 在售,入 URL);SKU 表(名称(固定左)/ 卡型 / 档位 / 切分规格 / 容量卡数 / 已售 / 实际超卖 / 算力超卖× / 单价(可排序,右对齐)/ **状态(在售 / 已下架)** / 操作(固定右:编辑 + 更多 ▾:上架 / 下架)),在架而容量为 0、实际超卖达上限时标红;**改价走 `useConfirm`**(变更行 + 影响面 + 范围说明,影响面查询在途时确认禁用),规格缺要素被拒时给「强制上架」出口(红色确认);编辑抽屉全参数表单(含 `包周期` 与 `竞价档` Switch,后者**新建默认关**,说明「开启后该规格可按竞价价售卖;竞价实例在容量紧张时会被平台回收」)+ 超卖风险文案挂在超卖字段的 `extra`(「变更仅影响新实例」)+ **编辑原因是最后一个字段**,右侧实时容量预览(sticky);footer 提交 + 取消,`useLeaveGuard`;新建可「从集群资源创建」。表单常量在 `-skuForm`。
- **租户与实例**(全宽;三个 Tab,拆在 `-TenantsTab` / `-InstancesTab` / `-DeletionsTab`):租户表(ID / 手机脱敏(固定左)/ 余额(右对齐)/ 累计消费 / 实例数 / 数据盘 GB / 状态 / 注册时间 / 操作(固定右):查看账务 + `冻结`·`解冻`(解冻只填原因);`FilterBar` 检索落审计,输入框 ↔ `?q=`)+ 点行开租户抽屉(**`?tenant=<id>` 入 URL**,`TenantLink` 与告警都直开;头部 `EntityHeader size="drawer"`:租户 ID / 手机 / 状态 / 余额 / 累计消费 / 实名 + 主动作 `冻结`·`解冻`;Tab `账务`(小时账单 + 资金流水 + 订单)/ `实例`(含「配额」编辑区)/ `在线服务` / `事件`,`?dtab=`;嵌套表 403 用 compact 空态;宽度 `drawerWidth.lg`);全局实例表(`FilterBar`:状态 / 实例名 / 节点名,带清除与 total)按形态标(开发机 / 在线服务,服务行链到 `/services?q=<slug>`),名称下副行 uuid 前 8 位 `Mono`,操作 `RowActions`:主动作 `强制停止`,更多 ▾ 里 `强制回收`(目标 = 实例名 · uuid 前缀);注销申请表:状态筛选入 URL,执行走 L3(键入用户 ID + 勾选 + 必填原因),驳回走 `ReasonAction`,已处理行显示处理人与时刻。
- **在线服务**(全宽):`FilterBar`(名称或 slug 前缀 / 状态 / 就绪 / 含已删除,入 URL);全局服务表(服务名 + slug `Mono`(固定左)/ 归属 / 服务端点主机名(`CopyField`)/ 状态徽标 + 就绪副行 / 当前实例(链到全局实例表按 uuid 检索)/ 版本 / 节点 / 创建时间 / 操作(固定右)),只读 + 唯一处置 `强制停止`(`ReasonAction`,目标 = 名称 · slug,委托当前版本实例 force-stop,仅 running / unready)。
- **镜像与预热**(全宽;页头新鲜度条,`POLL.ticket` 可暂停):镜像表(框架(固定左)/ Python / CUDA / 镜像地址 `CopyField` / 预热开关 / 覆盖率 / 操作(固定右):立即预热 · 编辑 + 更多(删除));预热说明进 `?` tooltip,不做常驻条;**关闭预热走 L1 确认**;覆盖率里的「N 台失败」可点,展开该行节点缓存面板(`?image=<id>` 入 URL,可直链);编辑抽屉 footer + `useLeaveGuard`,校验失败就地红字。
- **平台配置**(仅超管):左侧分组导航(安全 / 第三方渠道 / 基础设施 / 站点信息;导航项带状态点 + tooltip 与一行图例:红 = error、琥珀 = warning、绿 = 开关已开、灰 = 关闭或未配置;**有未保存修改的分组打点**;**当前分组 `?group=` 入 URL**)+ 顶部服务端配置风险告警经 `AttentionBar` 聚合(「前往」跳到对应分组,带回链)+ 右侧分组表单(字段标签、策略名与风险复述全部在 locales);「安全策略」页为开关行;全局「保存变更(N)」+ 原因必填,确认弹窗按分组列出变更,含关闭安全开关时红色复述风险。子文件:`-platformNav` / `-platformFields` / `-platformSecurity` / `-platformTestCards`。
- **财务对账**(全宽;Tab 拆在 `-OrdersTab` / `-RefundsTab` / `-InvoicesTab` / `-AdjustmentsTab` / `-SettlementGapsTab` / `-AnomaliesTab`,筛选态在 `-financeFilters`):日对账卡(日期 `?day=` 入 URL)`事件计费合计` vs `指标估算合计` + **差异率**,>2% 标红并列差异实例(链到实例 Tab);充值流水(`FilterBar`;行内 **核验**,补单按状态门控);退款(退款单号 `Mono` 固定左,行内 `批准` / `驳回` 或 `打款登记`,`取消` 收进更多;确认文案带退款单号与金额;打款不能是审批人;渠道筛选标「本页已加载」)| 发票(开具 / 驳回,文案带 #id 与金额;操作列固定右且为最后一列)| 调账(发起 → 双人复核:**批准需勾选「已核对租户与金额」**,驳回需理由;发起人不能自审;操作列固定右)| 结算缺口(筛选入 URL;单条 / **勾选批量**重放,核销走 `ReasonAction` 带缺口 #id)| 异常清单(丢回调 / 关单 / 负余额,可渠道核验与补单;核验结果对话框状态走映射表,底部直接给「补单」;负余额只给提示)。不内嵌审计 Tab(独立页)。
- **工单**:`FilterBar`(状态 / 分类 / user_id / 工单号,游标分页;「待回复 N」为常驻 Segmented 筛选)+ 详情抽屉(对话流 + 回复框贴底;`标记解决` / `关闭工单` 在抽屉 footer,走 `useConfirm`,标题带工单号);读全角色,写 ops / admin。
- **告警中心**(页头新鲜度条):表格(严重度(图标 + 文字)/ 标题 / 目标 / 时间 / 确认状态)+ `ListCapNote`;`severity` 服务端过滤、`确认状态` 在服务端返回窗口(`LIST_CAPS.alerts`,与后端 `admin_alert_stream` 的 limit 同值)内客户端过滤,两个筛选入 URL(`FilterBar`);未确认项带复选框,全选并入 `BulkBar` + **批量确认**;条目深链与 AlertBell / 总览告警流共用 `alertLink`;确认闭环同一范式(写限 ops/admin,成功后 `["admin","alerts"]` 前缀失效);已确认条目显示确认人与时刻,错误态用 `TableErrorEmpty` 如实显示。
- **系统设置**:策略参数(表格:参数(含单位与范围)/ 生效值(右对齐)/ 新值(右对齐),即时生效提示进确认框)/ 公告(发布 / 撤回走 `ReasonAction`,目标 = 标题)/ 法务文档(草稿 → 发布 → 归档,归档走 `ReasonAction`;**发布前若编辑器有未保存内容,先保存或阻止**,发布确认展示真实 diff)/ 管理员账号(建号 / **改角色**(`RowMoreMenu` 显式动作 → 确认)/ 停用 / **重置密码二次确认(目标 = 用户名,踢全部登录态)** / 重置两步验证走 `ReasonAction` 带用户名;用户名与操作列固定;自助改密与恢复码重新生成)。
- **审计**:`FilterBar`(limit + 游标翻页 + 分钟级时间窗);详情只在展开行渲染一次;审计只在本页,财务页不内嵌。
