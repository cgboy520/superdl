# 支付

充值单、微信/支付宝双渠道、回调与查单闭环。代码位于 billing 模块(`payment_channels.py` / `webhooks_router.py`)。

## 数据模型

- `orders`:order_no 唯一、user_id、type(recharge)、amount numeric(14,2) >0、channel(wechat/alipay/mock)、channel_txn_id 唯一?、status(pending/paid/closed/failed)、idempotency_key(与 user_id 联合唯一)、qr_url、paid_at、expires_at、发票字段预留(invoice_*)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/wallet/recharges` | user | `{amount, channel}` + Idempotency-Key → `{order_no, qr_url, expires_at}`;下单校验渠道开关 |
| `GET /api/v1/wallet/recharges/{order_no}` | user | 状态轮询,到终态(paid/closed/failed)即停 |
| `POST /api/v1/webhooks/wechatpay` | 渠道验签 | 验签 → channel_txn_id 幂等 → 事务{order.paid + 钱包入账 + ledger} |
| `POST /api/v1/webhooks/alipay` | 渠道验签 | 同上;应答体为纯文本 `success` |
| `POST /api/v1/webhooks/mock` | 仅 dev/test | 直接标记支付成功 |

## 规则与不变量

- 微信支付走 APIv3(wechatpayv3),支持微信支付公钥模式(`PUB_KEY_ID_*`,新商户唯一模式)与平台证书模式(存量商户)。
- 支付宝走当面付(alipay-sdk-python):precreate + RSA2 普通公钥模式,含查单。
- `PaymentChannel.query_order` 是各渠道统一查单 seam,mock 渠道自带渠道侧账本。
- 回调必须靠 `channel_txn_id` 唯一约束幂等,重放不重复入账;金额不匹配的回调拒绝并告警。
- 回调加时间戳新鲜度窗口 ±24h(微信 `Wechatpay-Timestamp` / 支付宝 `notify_time` 加签参数):覆盖渠道一天量级的重试节奏与关单后迟到回调的 rescue 路径。
- 微信回调除验签外核对 resource 的 mchid/appid 确为已方商户(缺失即判失败);支付宝验签串按官方口径剔除空值参数。
- 渠道构造(PEM/RSA 加载)经 `asyncio.to_thread` 出让事件循环,实例按配置指纹缓存。
- 支付宝回调应答必须是纯文本 `success`,不能返回 JSON。
- 下单必须向渠道传过期时间(微信 `time_expire` RFC3339 / 支付宝 `timeout_express` 分钟),与本地关单时间同步。
- 关单后到达、验签有效且金额一致的成功回调自动入账(与人工补单同等校验);failed 单与金额不符仍拒。指标 `superdl_payment_closed_order_rescued_total` 非零即说明本地关单 TTL 与渠道过期不同步。
- 查单 poller 每 2 分钟收敛丢回调(advisory lock 1007),不扫 closed 单;单笔入账失败(金额/渠道不符、唯一约束冲突)记 `superdl_payment_recover_failed_total` 后跳过,不中断整轮;残余窗口由异常清单 + 人工补单兜底。
- 人工补单为渠道核验制:服务端实时查渠道(锁外查询、显式超时 15s,落账前行锁内复核状态),已支付且金额一致才入账;pending / closed / failed 单均可补,渠道是唯一事实源。支持 Idempotency-Key:同键重放且已入账则回当前状态而非 409(落 `orders.backfill_idempotency_key`)。见 [admin.md](./admin.md)。
- 渠道凭据与开关在管理端配置,不入代码与 K8s Secret 之外的任何位置,见 [platform-config.md](./platform-config.md)。
- 不做渠道原路退款:退款单审批通过不动钱包,财务登记打款成功才同事务负向核销(审批与打款分人)。
- 已开票(issued)账期的 paid 订单不可申请退款,须先红冲。
