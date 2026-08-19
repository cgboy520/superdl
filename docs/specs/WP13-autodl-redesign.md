# WP13 · 用户端 AutoDL 化重设计(公开主页 + 全屏对齐)

规格来源:`docs/ui-ux-spec.md` §2/§3(本 WP 同步修订);方向决策经人工确认:
1. 布局/信息架构/交互对齐 AutoDL 实况,品牌视觉保持 SuperDL 原创(靛蓝 #4F46E5,原创文案与 logo);
2. 市场为「AutoDL 式筛选链 + 表格单选」交互,数据行 = SKU(不引入地域/主机概念);
3. 新增完整公开主页(未登录默认落地页)与登录分屏;
4. 允许小幅只读后端补充(策略常量/当日消费/批量指标),OpenAPI-first。

## 目标

- 公开层:`/` 营销主页(Hero/四宫格/实时价格墙/算力排名/CTA/三栏页脚)、`/login` 左品牌右表单分屏;概览迁 `/dashboard`。
- 控制台壳:全宽渐变靛蓝顶栏(56px) + 浅色可折叠侧栏(lg 断点),AutoDL「顶栏压侧栏」结构。
- 逐屏:市场(筛选链+SKU 表格+底部结算条)/创建(单栏卡片流+结算条,数据盘行内直建,SSH 行内添加)/实例列表(7 列:含 GPU 利用率 sparkline、今日消费、快捷工具竖排;更多菜单 3 可用+3 预留灰置)/详情(修路由不可达 bug+今日消费)/费用(真二维码+渠道 Tab 预留+客户端 CSV 导出)/存储(计费快照价列+到期倒计时列)/设置(实名预留+保存按钮)。
- 设计系统:tokens 全量扩展(fontFamily/语义色/components 级)、全局 styles.css(reset+tabular-nums)、favicon、硬编码色收敛。

## 契约(新增 3 只读端点,零迁移)

| 端点 | 模块 | 说明 |
|---|---|---|
| `GET /api/v1/policies` | billing | 公开;盘价(Decimal 串)/盘容量上下限/宽限与冻结天数/冻结 72h/默认预警阈值 |
| `GET /api/v1/bills/daily-summary?date=YYYY-MM-DD&tz_offset_minutes=480` | billing | 本地日界折 UTC 聚合 BillHourly(按实例)+当日 BillDailyDisk |
| `GET /api/v1/metrics/instances` | metering | 本人 running 实例(cap 20)近 1h gpu_util 稀疏序列;断源返回 200 `{available:false}`(不 503);路径避开 `/instances/*` 前缀防被 `{uuid}` 路由吞掉 |

前端 URL 契约不变:`/login /market /market/create/:skuId /instances /instances/:uuid /storage /billing /settings`;`/` 由概览让位给公开主页,概览迁 `/dashboard`。

## 数据变更

无(零迁移;`alembic check` 应无 diff)。

## 明确不做(负范围)

包周期后端/无卡模式/保存镜像(UI 仅 disabled 预留)、地域与主机级库存、优惠券/发票/会员折扣、存储续费按钮(按日扣费制无预付到期)与已用量列(无数据源)、`GET /instances` 分页、admin 端、真实支付渠道。

## 验收用例

1. 未登录访问 `/` 见完整主页,价格墙为 `/skus` 实时数据,`/skus` 失败时该区降级为入口按钮不开天窗;点击 SKU CTA 跳 `/login?redirect=...`,登录后回跳。
2. 已登录访问 `/` 仍显示主页,顶栏出现「进入控制台」。
3. `/instances/:uuid` 直接刷新可达(路由 bug 修复回归);事件 Tab 明示计费依据。
4. 市场页:库存 0 行灰置不隐藏;包日/周/月 chips 禁用带「即将上线」tooltip;未登录可浏览,结算条 CTA 为「登录后租用」。
5. 创建页:选「新建数据盘」可行内直建并随实例挂载(建盘成功而建实例失败时提示数据盘已计费可管理);无 SSH 密钥时行内添加。
6. 实例列表:running 行显示近 1h sparkline 与今日消费;Prometheus 停机时该列显示「监控暂不可用」且页面其余正常。
7. 充值 modal 渲染真二维码(.ant-qrcode);微信/支付宝 Tab 禁用带原因。
8. 存储:grace/frozen 行显示天级倒计时(由 policies 天数前端计算);计费列显示每盘快照价。
9. Playwright 冒烟全流程绿(含新增详情页段);后端 ruff/pyright/pytest(billing≥90%)/lint-imports 绿;前端 eslint/tsc/vitest/build 绿。

## 附录 A · ui-ux-spec.md 修订条目(本 WP 已执行)

- §2 布局行:改「全宽品牌渐变顶栏+可折叠浅色侧栏」;补 fontFamily/全局 tabular-nums/components token 说明。
- §3.1:顶部加 `首页 /(公开)` 与 `登录 /login(分屏)`;概览标注 `/dashboard`。
- 新增 §3.0 公开主页与登录规格。
- §3.2:卡片网格 → 筛选链 chips+SKU 表格单选+底部结算条;计费方式 chips 预留落地。
- §3.3:左右分栏 → 单栏卡片流+底部结算条;数据盘行内直建;镜像 Tab 增「我的镜像(预留)」。
- §3.4:定稿 7 列;更多菜单 3 可用+3 预留清单;搜索为客户端过滤。
- §3.5:头部信息条补「今日消费」。
- §3.6:渠道 Tab(微信/支付宝预留)+真二维码+客户端 CSV;今日消费。
- §3.7:列定稿(计费快照价/到期倒计时);「已用量待后端」「无续费(计费模型差异)」注记。
- §3.8:新增 copy 键(comingSoon/计费规则等)与 `formatDaysLeft`。
- §5 P1 追加:SKU total_count 与 TFLOPS 字段、会员折扣、微信登录、每路由动态 title、市场筛选 URL 参数化、数据盘已用量上报。
