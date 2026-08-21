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
