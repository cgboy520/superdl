# JuiceFS 元数据引擎迁移:Redis → PostgreSQL(P0-13)

新装集群直接用 PG(`values/juicefs.yaml` 头注释);本手册面向**存量 Redis 引擎集群**的
在线迁出。原理:JuiceFS 自带元数据导出/导入(`juicefs dump` / `juicefs load`),
对象存储里的数据块不动,只搬元数据。

## 铁律

- **停写窗口内迁移**:dump 是时点快照,窗口内的写入不会进新引擎;窗口越短越好,
  但必须全程零写入(否则窗口内变更在切换后丢失,且回退会产生分叉)。
- **先验证后切换,旧引擎保留**:Redis 数据至少保留 7 天只读,作为回退底牌。
- 全程用与线上同大版本的 `juicefs` CLI(1.4.x LTS),跨版本 dump/load 不保证兼容。

## 前置准备

1. 目标 PG:独立 database + 独立最小授权账号(与平台库同实例或独立实例均可):
   ```sql
   CREATE USER juicefs WITH PASSWORD '<强口令>';
   CREATE DATABASE juicefs_meta OWNER juicefs;
   -- 禁止授予其他库任何权限;连接串强制 sslmode=require
   ```
2. PG 备份体系已覆盖该库(WAL 归档 + 每日备份,见 `pg-backup-restore.md`)。
3. 公告停写窗口(建议业务低谷,预留 2×dump 耗时 + 1h 校验余量)。
4. 准备一台能同时触达 Redis 与 PG 的运维机,装好 juicefs CLI。

## 迁移步骤

```bash
# 1. 进入停写窗口:禁止新建实例(管理端维护模式),全部租户实例关机,
#    确认无残留挂载(kubectl -n kube-system get pods -l app=juicefs-mount 应为空)。
#    平台 api/worker 不停,但编排链路在维护模式下不再产生挂载动作。

# 2. 导出 Redis 元数据(窗口起点;此后 Redis 必须零写入)
juicefs dump 'redis://:<password>@redis-sentinel:26379/1' meta.json
ls -lh meta.json   # 记录大小与行数,校验时对照

# 3. 导入 PG(空库;重复执行前须 DROP 重建 juicefs_meta,load 不做合并)
juicefs load 'postgres://juicefs:<password>@<pg-host>:5432/juicefs_meta?sslmode=require' meta.json

# 4. 校验(全部通过才切换):
#    - dump/load 的 entry 统计一致(stdout 汇总行对照);
#    - 元数据自检:juicefs fsck 'postgres://…/juicefs_meta?sslmode=require'
#    - 只读挂载新引擎抽验:juicefs mount --read-only <pg-metaurl> /mnt/jfs-check
#      抽 10 个租户数据盘目录核对文件数/总容量与源一致,抽 3 个文件 md5 比对;
#    - 卸载:juicefs umount /mnt/jfs-check

# 5. 切换:更新 Secret 的 metaurl(对象存储凭据不变)
kubectl -n kube-system create secret generic superdl-juicefs-secret \
  --from-literal=name=superdl-data \
  --from-literal=metaurl='postgres://juicefs:<password>@<pg-host>:5432/juicefs_meta?sslmode=require' \
  --from-literal=storage=oss \
  --from-literal=bucket='https://<bucket>.<oss-endpoint>' \
  --from-literal=access-key=<ak> --from-literal=secret-key=<sk> \
  --dry-run=client -o yaml | kubectl apply -f -

# 6. 逐台生效:metaurl 只在新建挂载时读取。逐节点重启 CSI mount pod
#    (删除 mount pod 由 CSI 自动重建,等该节点挂载恢复再下一台),分批灰度。

# 7. 恢复营业:抽 1 个测试实例开机验证数据盘读写 → 解除维护模式 → 窗口结束。
```

## 回退(窗口内或切换后早期发现异常)

1. 重新进入停写窗口(同步骤 1)。
2. Secret 的 metaurl 改回原 Redis 连接串,逐台重建 mount pod(同步骤 6)。
3. 若切换后 PG 引擎已产生新写入:先 `juicefs dump <pg-metaurl> meta-new.json`
   回灌 Redis(`juicefs load`,窗口内操作),再切回;无法回灌时按差异清单人工补数。
4. 复盘后才允许二次迁移。

## 收尾

- Redis 引擎保留 7 天只读(禁写),期间每日 `juicefs fsck` 新引擎一次;
  无异常后下线 Redis,`meta.json` 与切换当日 PG 逻辑备份一并归档。
- 更新平台文档/资产管理中该集群的元数据引擎登记(Redis → PG)。
