# 数据盘

JuiceFS 子路径数据盘:独立生命周期、配额、扩容与日结。表与代码在 orchestrator 模块。

## 数据模型

- `data_disks`:uuid、user_id、name、size_gb、juicefs_subpath、status(active/grace/frozen/deleting/deleted)、expires_at?、mounted_instance_id?

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/disks` | user | `{name, size_gb}`;钱包行锁临界区内校验余额(在途+新增日费)与数量配额 |
| `GET /api/v1/disks` | user | 列表 |
| `PATCH /api/v1/disks/{uuid}` | user | 扩容,只增不减;缩容报 `DISK_SHRINK_FORBIDDEN` |
| `DELETE /api/v1/disks/{uuid}` | user | 挂载中(running/starting/creating/stopping/releasing 实例)删除报 `DISK_IN_USE`;挂载实例已 stopped/frozen/failed(Pod 不在)时放行并自动解挂 |

## 规则与不变量

- 数据盘生命周期与实例解耦:实例释放不影响数据盘,同一盘可先后被不同实例挂载(挂载点 `/root/data`)。
- 欠费策略:欠费 → grace 宽限(默认 7 天)→ frozen(默认 30 天)→ 清除,天数为可调策略参数。**grace 停计费**(进入 grace 时先结清在账天数);宽限钟 `grace_started_at` 首次欠费起算,回款恢复不归零;冻结删除钟每次进 frozen 重新起算。
- 按自然日计费,不足一日按一日;关机也扣。日结每日 00:10 UTC 执行(advisory lock),结算上一自然日,按 `bills_daily_disk` UNIQUE(disk_id, day) 幂等扣款,漏掉的日期由 `settlement_watermarks` 水位线追平。
- 删盘与扩容前先按**变更前容量**结清尚未出账的自然日,下界取水位线。
- `grace` / `frozen` 态不计费,删除冻结盘不补账。
- `size_gb` 是计费与逻辑口径:JuiceFS 目录配额未下发集群(worker 镜像尚无 juicefs CLI 与权限),超写只靠日结价格约束。
- 每用户数量上限 `SUPERDL_MAX_DISKS_PER_USER`(默认 20);建盘只校验余额,当天不扣款。
- 删除的多级防护在前端,后端校验挂载状态。
- 幂等键 24h 窗口:窗口内重放返回既有盘,窗外同键按新单(旧记录让出键位);并发同键靠唯一约束收敛,不多开。
