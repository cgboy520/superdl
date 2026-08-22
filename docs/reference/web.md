# 用户控制台

`apps/web`:公开层 + 控制台六屏。视觉规格见 `docs/ui-ux-spec.md`。

## 契约

| 路由 | 鉴权 | 说明 |
|---|---|---|
| `/` | 公开 | 营销主页:Hero / 四宫格 / 实时价格墙 / 算力排名 / CTA / 三栏页脚 |
| `/login` | 公开 | 左品牌右表单分屏,支持 `?redirect=` 回跳 |
| `/legal/terms` `/legal/privacy` `/help` | 公开 | 合规与帮助 |
| `/dashboard` | 登录 | 概览 |
| `/market` | 公开可浏览 | 筛选链 + SKU 表格单选 + 底部结算条;CTA 即库存 |
| `/market/create/:skuId` | 登录 | 单栏卡片流:规格/镜像级联/数据盘(可行内直建)/SSH 公钥(可行内添加)/名称 + 结算条;经济档知情同意 |
| `/instances` | 登录 | 登录后默认落地页;表格含状态徽标(冻结倒计时)、利用率 sparkline、今日消费、SSH 复制、Jupyter 直达 |
| `/instances/:uuid` | 登录 | 监控 / 连接 / 事件时间线 / 账单 四 Tab + 危险区释放 |
| `/billing` | 登录 | 余额卡 + 充值 modal(二维码轮询)+ 消费概览 + 账单/收支明细/CSV 导出 |
| `/storage` | 登录 | 挂载全景图 + 数据盘列表(扩容抽屉、到期倒计时、多级删除防护) |
| `/settings` | 登录 | SSH 公钥、通知阈值、实名入口 |

## 规则与不变量

- 服务端状态全走 TanStack Query + 生成 hooks,禁止手写 fetch;文案与状态映射走 `packages/ui`;antd 6 原生组件自封装,不引 pro-components。
- 401 由 mutator 静默续期并重放(single-flight);续期失败才跳登录并带回跳。
- 查询失败不得伪装成数据:统一走 `components/QueryState.tsx` 的表格错误态与页级横幅,金额未就绪显示 `—`,详情页加载失败为错误横幅 + 重试而非整页白屏。
- 余额与金额比较走 `compareAmounts`(BigInt),盘费日估算走 `ui.diskDailyEstimate`,前端不做 float 运算。
- 创建失败必须闭环:failed 行内给原因与未扣费说明并提供重新创建;`NO_CAPACITY` 给引导;重提前重新生成 Idempotency-Key。
- 建盘成功而建实例失败时提示数据盘已计费可管理。
- 充值轮询到终态(paid/closed/failed)即停;渠道 Tab 随渠道开关启用,未开启时禁用并给原因。
- 监控断源时该列/该区降级为「监控暂不可用」,页面其余部分照常。
- 库存为 0 的行灰置不隐藏;未上线能力(包周期 chips、更多菜单预留项)禁用并带说明,不隐藏。
- 月份等日期按本地时区计算;`/instances/:uuid` 直接刷新可达。
- Jupyter `window.open` 必须带 `noopener,noreferrer`。
- echarts 按需注册收口在 `components/EChart.tsx`;design tokens 与全局 `styles.css` 统一,不留硬编码色。
