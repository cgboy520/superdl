# WP5 · 支付

## 目标
充值单 + 微信/支付宝双渠道 + 回调幂等。dev 走 mock 渠道,真实联调为人工事项 #7。

## 数据
- `orders`:order_no 唯一、user_id、type(recharge)、amount、channel(wechat/alipay/mock)、channel_txn_id 唯一?、status(pending/paid/closed/failed)、idempotency_key 唯一?、qr_url、paid_at、created_at;发票字段预留(invoice_*)

## API
- `POST /wallet/recharges` {amount, channel}(Idempotency-Key)→ {order_no, qr_url, expires_at}
- `GET /wallet/recharges/{order_no}` → 状态轮询
- `POST /webhooks/wechatpay` / `POST /webhooks/alipay`:验签 → channel_txn_id 幂等 → 事务{order.paid + 钱包入账 + ledger}
- mock 渠道:`POST /webhooks/mock` 直接标记支付成功(仅 dev/test)

## 验收
- 重放回调不重复入账(channel_txn_id 唯一约束)
- 相同 Idempotency-Key 重复下单返回同一订单
- 金额不匹配的回调拒绝并告警
- 渠道 SDK:wechatpayv3 2.0.x / alipay-sdk-python 3.7.x,验签失败 400
