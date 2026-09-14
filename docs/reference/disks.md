# 数据盘

JuiceFS 子路径数据盘:独立生命周期、配额、扩容与日结。表与代码在 orchestrator 模块。

> **runc 档(shared/cpu,即 hami / mig / cpu 池)当前挂不上**:这些 Pod 带 `hostUsers: false`(userns),而 userns Pod 挂卷要求 idmapped mount,JuiceFS 的 FUSE 不支持——实例挂盘与 `disk.wipe` 都起不来(`failed to set MOUNT_ATTR_IDMAP ... invalid argument`)。CSI 的 sidecar/serverless 模式不是出路:挂载传播跨不过 userns 边界,应用容器拿到的是 emptyDir,写入静默丢失。**dedicated(kata)档不带 userns,不受此限**(该链路未实测)。建盘不分档位放行并即刻计费,取舍见 `../decisions.md`。

## 数据模型

- `data_disks`:uuid、user_id、name、size_gb、juicefs_subpath、status(active/grace/frozen/deleting/deleted)、mounted_instance_id?、quota_synced

## 契约

| 端点                          | 角色/鉴权 | 说明                                                                                                                        |
| ----------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/disks`          | user      | `{name, size_gb}`;钱包行锁内校验余额(在途+新增日费)与数量配额                                                               |
| `GET /api/v1/disks`           | user      | 列表                                                                                                                        |
| `PATCH /api/v1/disks/{uuid}`  | user      | 扩容,只增不减;缩容报 `DISK_SHRINK_FORBIDDEN`                                                                                |
| `DELETE /api/v1/disks/{uuid}` | user      | 挂载中(running/starting/creating/stopping/releasing 实例)报 `DISK_IN_USE`;挂载实例已 stopped/frozen/failed 时放行并自动解挂 |

## 规则与不变量

- 数据盘与实例解耦:实例释放不影响数据盘,同一盘可先后被不同实例挂载(挂载点 `/root/data`)。
- 欠费(判据可用余额 ≤ 0,与实例欠费巡检同口径,见 [billing.md](./billing.md))→ grace → frozen → 清除,天数为策略参数,见 [limits.md](./limits.md)。grace 停计费(进入 grace 时先结清在账天数);宽限钟 `grace_started_at` 首次欠费起算,回款不归零;冻结删除钟每次进 frozen 重新起算。
- 按北京自然日计费(日界取 `app/core/timeutil.py` 的 `billing_day_floor`),不足一日按一日;关机也扣。日结每日 UTC 16:10(advisory lock),结算上一自然日,按 `bills_daily_disk` UNIQUE(disk_id, day) 幂等,漏掉的日期由 `settlement_watermarks` 追平。
- 删盘与扩容前先按变更前容量结清尚未出账的自然日,下界取水位线。
- `grace` / `frozen` 态不计费,删除冻结盘不补账。
- `size_gb` 既是计费口径也是 JuiceFS 目录硬配额:建盘与扩容同事务 enqueue `disk.quota`,worker(disk-ops)在平台 ns(`SUPERDL_K8S_PLATFORM_NAMESPACE`,默认 `superdl`)起 juicefs CLI Job(镜像 `SUPERDL_JUICEFS_CLI_IMAGE`;metaurl 经 `superdl-db` 的 `juicefs-metaurl` 键 secretKeyRef 注入)执行 `juicefs quota set`,成功置 `quota_synced=true`;重试耗尽转死信后由 reconciler 周期重派(失败计 `superdl_juicefs_quota_failed_total`);删盘先 `quota delete` 再擦除。
- 每用户数量上限走「用户级覆盖 → 策略 `max_disks_per_user` → env」三层链,数值见 [limits.md](./limits.md);建盘只校验余额,当天不扣款。
- 删除的多级防护在前端,后端校验挂载状态。
- 幂等键 24h 窗口:窗内重放返回既有盘,窗外同键按新单;并发同键靠唯一约束收敛。
