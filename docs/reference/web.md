# 用户控制台

`apps/web`:公开层 + 控制台七屏。视觉规格见 `docs/ui-ux-spec.md`。

## 契约

| 路由 | 鉴权 | 说明 |
|---|---|---|
| `/` | 公开 | 营销主页:Hero / 四宫格 / 实时价格墙 / 算力排名 / CTA / 三栏页脚 |
| `/login` | 公开 | 左品牌右表单分屏,支持 `?redirect=` 回跳 |
| `/legal/terms` `/legal/privacy` `/legal/deletion-notice` `/help` | 公开 | 合规与帮助;正文取后端当前 published 版,en-US 缺失回落 zh-CN |
| `/dashboard` | 登录 | 概览 |
| `/market` | 公开可浏览 | 筛选链 + SKU 表格单选 + 底部结算条;CTA 即库存;「计费方式」= 按量 / 包日 / 包周 / 包月 / 包年 / 竞价,后五者带折扣角标(折扣从 `/policies` 读)。选中周期后结算条显示周期总价并把 `?period=` 带进创建页;选中竞价后结算条显示折后时价 + 原价划线,并常驻一条「竞价实例在容量紧张时会被平台回收」的提示 |
| `/market/create/:skuId` | 登录 | 单栏卡片流:规格/镜像级联/数据盘(可行内直建)/SSH 公钥(可行内添加)/名称 + 结算条;经济档知情同意。`?period=` 承接市场页的计费方式,选了周期即提交 `market/period/period_count`;**选了竞价则在提交前弹竞价知情同意 modal**(五条 + 必勾复选框,与经济档同款组件),提交 `market='spot'`、不带 `period` |
| `/market/create/:skuId?workload=service` | 登录 | 同一条卡片流的服务形态:容器(镜像/启动命令/启动参数/环境变量)+ 对外服务(端口/协议/健康检查/访问鉴权)+ 可选 SSH;主按钮「部署服务」 |
| `/instances` | 登录 | 登录后默认落地页;表格含状态徽标(冻结倒计时)、利用率 sparkline、今日消费/包周期到期、SSH 复制、Jupyter 直达;包周期实例在「更多」里多「续费」(modal)与「自动续费」开关,按量实例多「转包周期」(同形 modal,一次性预扣),竞价实例多「转按量」(确认弹窗,免被回收),页头有临期横幅 |
| `/instances/:uuid` | 登录 | 监控 / 服务 / 连接 / 日志 / 事件时间线 / 账单 Tab + 危险区释放;包周期实例页头多一个到期标签,已到期时开机按钮禁用并提示先续费;**服务** Tab 仅 `workload_type='service'` 渲染(端点 URL、API Key 表与一次性新建 modal、调用示例、容器配置回显),**连接** Tab 在服务形态下按 `with_ssh` 决定是否出 SSH 卡片、一律不出 Jupyter 卡片 |
| `/billing` | 登录 | 余额卡 + 充值 modal(二维码轮询)+ 消费概览 + 账单/收支明细/退款/发票 + CSV 导出 |
| `/storage` | 登录 | 挂载全景图 + 数据盘列表(扩容抽屉、到期倒计时、多级删除防护) |
| `/settings` | 登录 | SSH 公钥、通知阈值、实名入口、危险区账号注销 |
| `/support` | 登录 | 自助排查 FAQ + 联系客服 + 我的工单 |
| `/support/:ticketId` | 登录 | 工单对话流(回复 / 关闭) |
| `/notifications` | 登录 | 通知中心:全部/未读筛选(`?filter=` 入 URL)+ 行点击已读并跳转 + 全部已读;跳转优先结构化 `target_id` 精确深链(instance/preempted/subscription/gpu_fault → 实例详情,ticket → 工单对话),无 `target_id` 按类型落列表页 |

## 规则与不变量

- 服务端状态全走 TanStack Query,请求一律用生成的 fetcher(hooks 在 `api/queries.ts` / `api/mutations.ts` 自建),禁止手写 fetch;文案与状态映射走 `packages/ui`;antd 6 原生组件自封装,不引 pro-components。
- `src/routes/` 目录下的非路由文件(测试/工具)必须以 `-` 开头(tanstack router 的 routeFileIgnorePrefix),否则会被误收入路由树并告警;antd 6 已废弃的 props(如 `maskClosable` → `mask={{closable}}`)按 deprecation 警告即时迁移,不留存量。
- 401 由 mutator 静默续期并重放(single-flight);续期失败才跳登录并带回跳。
- 查询失败不得伪装成数据:统一走 `components/QueryState.tsx` 的表格错误态与页级横幅,金额未就绪显示 `—`,详情页加载失败为错误横幅 + 重试而非整页白屏。
- 余额与金额比较走 `compareAmounts`(BigInt),盘费日估算走 `ui.diskDailyEstimate`,前端不做 float 运算。
- 创建失败必须闭环:failed 行内给原因与未扣费说明并提供重新创建;`NO_CAPACITY` 给引导;重提前重新生成 Idempotency-Key。建盘成功而建实例失败时提示数据盘已计费可管理。
- 充值轮询到终态(paid/closed/failed)即停;渠道 Tab 随渠道开关启用,未开启时禁用并给原因。
- 监控断源时该列/该区降级为「监控暂不可用」,页面其余部分照常。
- 库存为 0 的行灰置不隐藏;条件操作一律可见但禁用并带 tooltip 说明原因,不隐藏。**占位项的去留有判据**(见 [../ui-ux-spec.md](../ui-ux-spec.md) §1 规则 2):已排期的留 disabled 占位并注「即将上线」,没排期的不进 UI。周期 chip 只在选中规格 `period_enabled=false` 时置灰,竞价 chip 只在 `spot_enabled=false` 时置灰,两句同一条句式 —— **是规格不支持,不是功能没上线**。
- **折扣、到期预警天数与抢占宽限窗一律从 `GET /api/v1/policies` 读,禁止前端硬编码**:竞价知情同意里的折扣比例与「提前 N 秒通知」是给用户的承诺,取自 `spot_discount_pct` / `spot_grace_seconds`。
- **「转按量」的确认弹窗必须写明两件事**:转换后不再被回收,以及**当前整点小时将整体改按按量价结算**(`bills_hourly` 一小时只有一个单价;转换必然涨价,只补扣不退款,口径见 [billing.md](./billing.md))。它不带 `Idempotency-Key`(目标状态唯一,没有重复扣款风险)。
- 「转包周期」只对按量实例可用,是一次性预扣的支付动作,confirm 文案要写清先结清按量费用、转换后不退款。
- **包周期的金额以服务端报价为准。** 下单与续费的响应带完整报价三件套(原价 / 优惠 / 应付),前端逐行渲染、不自己做乘法;市场页与创建页在提交前只能按 `/policies` 的折扣算展示值,并注明以最终报价为准。续费与转包周期的请求都必须带 `Idempotency-Key`(每次打开 modal 生成一个)。
- 竞价实例被抢占时状态走 `stopping → stopped`,与用户自己关机在状态上没有区别,**事件时间线的 reason 映射必须覆盖 `preempted`** —— 那是用户唯一能分辨「这台是被平台回收的」的地方。
- 到期信息已内联在 `InstanceOut.subscription` 里(列表一次批量回填),列表页**不得**为它逐行再打接口。
- 服务端点的就绪为「否」时**不当故障渲染**:服务型实例持续 not-ready 也留在 running,如实显示并提示检查容器日志与健康检查路径(见 [services.md](./services.md))。
- 月份等日期按本地时区计算;`/instances/:uuid` 直接刷新可达。
- Jupyter `window.open` 必须带 `noopener,noreferrer`。
- echarts 按需注册收口在 `components/EChart.tsx`;design tokens 与全局 `styles.css` 统一,不留硬编码色。
