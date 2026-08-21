# WP8 · 数据盘

## 目标
JuiceFS 子路径数据盘:独立生命周期、配额、日结、扩容。

## 数据
- `data_disks`:uuid、user_id、name、size_gb、juicefs_subpath、status(active/grace/frozen/deleting/deleted)、expires_at?、mounted_instance_id?、created_at
- 到期策略:到期→7 天宽限(只读)→冻结 30 天→清除(参数化配置)

## API
- `POST /disks` {name, size_gb}(校验余额≥1 日费用)、`GET /disks`、`PATCH /disks/{id}` 扩容(只增不减)、`DELETE /disks/{id}`(多级防护在前端,后端校验未挂载)
- 创建实例时可挂载(挂载点 /root/data);实例释放不影响数据盘
- 日结:每日 24:00 UTC(advisory lock)按 `bills_daily_disk` UNIQUE(disk_id, day) 幂等扣款;关机也扣

## 验收
- 跨实例挂载(A 释放后 B 挂载同一盘)
- 缩容请求报 DISK_SHRINK_FORBIDDEN;挂载中删除报 DISK_IN_USE
- 日结重复执行零重复扣款;欠费盘进入 grace→frozen 链路

## 计费口径(现状)

- 按自然日计费,不足一日按一日。日结覆盖上一自然日,由 `settlement_watermarks` 水位线追平漏掉的日期。
- 删盘、扩容前先按**变更前容量**结清尚未出账的自然日:否则「当日建当日删」可循环零费用占用存储,
  扩容则会把新容量追溯到旧日期(多扣用户)。下界取水位线,欠费冻结期这类有意不计费的日子不补。
- `frozen` 态不计费(欠费链路),删除冻结盘不补账。
- 每用户数量上限 `SUPERDL_MAX_DISKS_PER_USER`(默认 20):建盘只校验余额、当天不扣款,不设上限即可无限建。

## ui-ux-spec 补充(该文档禁改,差异记录于此)

- §「挂载全景图」列 `/public/models(模型缓存·只读·免费)`:后端未实现该挂载(Pod 只挂实例盘与数据盘),
  已从前端全景图撤除。要恢复宣传须先实现只读挂载(JuiceFS 公共子路径 + Pod 侧只读挂载点)。
