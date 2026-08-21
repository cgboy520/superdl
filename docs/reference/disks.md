# 数据盘

JuiceFS 子路径数据盘:独立生命周期、配额、扩容与日结。表与代码在 orchestrator 模块。

## 数据模型

- `data_disks`:uuid、user_id、name、size_gb、juicefs_subpath、status(active/grace/frozen/deleting/deleted)、expires_at?、mounted_instance_id?

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/disks` | user | `{name, size_gb}`;校验余额 ≥1 日费用 |
| `GET /api/v1/disks` | user | 列表 |
| `PATCH /api/v1/disks/{uuid}` | user | 扩容,只增不减;缩容报 `DISK_SHRINK_FORBIDDEN` |
| `DELETE /api/v1/disks/{uuid}` | user | 挂载中删除报 `DISK_IN_USE` |

## 规则与不变量

- 数据盘生命周期与实例解耦:实例释放不影响数据盘,同一盘可先后被不同实例挂载(挂载点 `/root/data`)。
- 到期策略:到期 → 7 天宽限(只读)→ 冻结 30 天 → 清除,天数为可调策略参数。
- 日结每日 00:10 UTC 执行(advisory lock),结算上一自然日,按 `bills_daily_disk` UNIQUE(disk_id, day) 幂等扣款;关机也扣。
- 按自然日计费,不足一日按一日;日结覆盖上一自然日,漏掉的日期由 `settlement_watermarks` 水位线追平。
- 删盘与扩容前必须先按**变更前容量**结清尚未出账的自然日:否则「当日建当日删」可循环零费用占用存储,扩容则会把新容量追溯到旧日期而多扣用户。下界取水位线。
- `frozen` 态不计费,删除冻结盘不补账;欠费冻结期这类有意不计费的日子不做补账。
- 每用户数量上限 `SUPERDL_MAX_DISKS_PER_USER`(默认 20):建盘只校验余额、当天不扣款,不设上限即可无限建。
- 删除的多级防护在前端,后端只校验未挂载。
