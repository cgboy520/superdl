# WP13 · 用户端 AutoDL 化(公开主页 + 控制台壳 + 逐屏)

规格来源:`docs/ui-ux-spec.md` §2/§3。决策:布局/信息架构/交互对齐 AutoDL 实况,品牌视觉保持 SuperDL 原创(靛蓝 #4F46E5,原创文案与 logo);市场为「筛选链 + 表格单选」,数据行 = SKU,不引入地域/主机概念。

## 目标

- 公开层:`/` 营销主页(Hero/四宫格/实时价格墙/算力排名/CTA/三栏页脚)、`/login` 左品牌右表单分屏;概览在 `/dashboard`。
- 控制台壳:全宽渐变靛蓝顶栏(56px)+ 浅色可折叠侧栏(lg 断点),「顶栏压侧栏」结构。
- 逐屏:市场(筛选链+SKU 表格+底部结算条)/ 创建(单栏卡片流+结算条,数据盘行内直建,SSH 行内添加)/ 实例列表(7 列:GPU 利用率 sparkline、今日消费、快捷工具竖排;更多菜单 3 可用+3 预留灰置)/ 详情(含今日消费)/ 费用(真二维码+渠道 Tab+客户端 CSV 导出)/ 存储(计费快照价列+到期倒计时列)/ 设置(实名预留+保存按钮)。
- 设计系统:tokens(fontFamily/语义色/components 级)、全局 styles.css(reset+tabular-nums)、favicon,不留硬编码色。

## 契约(只读端点)

| 端点 | 模块 | 说明 |
|---|---|---|
| `GET /api/v1/policies` | billing | 公开;盘价(Decimal 串)/盘容量上下限/宽限与冻结天数/冻结 72h/默认预警阈值 |
| `GET /api/v1/bills/daily-summary?date=YYYY-MM-DD&tz_offset_minutes=480` | billing | 本地日界折 UTC 聚合 BillHourly(按实例)+当日 BillDailyDisk |
| `GET /api/v1/metrics/instances` | metering | 本人 running 实例(cap 20)近 1h gpu_util 稀疏序列;断源返回 200 `{available:false}`(不 503);路径避开 `/instances/*` 前缀,防被 `{uuid}` 路由吞掉 |

前端路由:`/ /login /dashboard /market /market/create/:skuId /instances /instances/:uuid /storage /billing /settings`;`/` 为公开主页。

## 数据变更

无。

## 明确不做(负范围)

包周期后端/无卡模式/保存镜像(UI 仅 disabled 预留)、地域与主机级库存、优惠券/发票/会员折扣、存储续费按钮(按日扣费制无预付到期)与已用量列(无数据源)、`GET /instances` 分页。

## 验收用例

1. 未登录访问 `/` 见完整主页,价格墙为 `/skus` 实时数据,`/skus` 失败时该区降级为入口按钮不开天窗;点击 SKU CTA 跳 `/login?redirect=...`,登录后回跳。
2. 已登录访问 `/` 仍显示主页,顶栏出现「进入控制台」。
3. `/instances/:uuid` 直接刷新可达;事件 Tab 明示计费依据。
4. 市场页:库存 0 行灰置不隐藏;包日/周/月 chips 禁用带「即将上线」tooltip;未登录可浏览,结算条 CTA 为「登录后租用」。
5. 创建页:选「新建数据盘」可行内直建并随实例挂载(建盘成功而建实例失败时提示数据盘已计费可管理);无 SSH 密钥时行内添加。
6. 实例列表:running 行显示近 1h sparkline 与今日消费;Prometheus 停机时该列显示「监控暂不可用」且页面其余正常。
7. 充值 modal 渲染真二维码(.ant-qrcode);微信/支付宝 Tab 随渠道开关启用,未开启时禁用带原因。
8. 存储:grace/frozen 行显示天级倒计时(由 policies 天数前端计算);计费列显示每盘快照价。
