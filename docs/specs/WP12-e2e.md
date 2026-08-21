# WP12 · E2E 演练

## 目标
一条脚本跑通全生命周期(FakeOrchestrator + mock 支付):
注册 → 充值 → 买共享档实例 + 数据盘 → 获取 SSH/Jupyter 接入信息 → 停机(尾账)→
查账单与事件时间线 → 释放(擦盘事件)→ 数据盘保留 → 余额/流水对得上。

## 形态
- `apps/api/tests/test_e2e_lifecycle.py`:API 级端到端(pytest,唯一事实来源)
- `e2e/tests/smoke.spec.ts`:Playwright 浏览器冒烟(注册→充值→开实例→账单→释放)

## 验收
- 演练脚本一次通过;全程金额与 ledger 自洽(消费合计 = 充值 - 余额)
- 实机集群版演练(真 K8s/真支付 1 分钱)属人工事项 #7/#8,脚本同款可复用
