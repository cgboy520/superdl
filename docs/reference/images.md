# 镜像与预热

平台镜像目录的管理端 CRUD、集群内 P2P 缓存与逐节点预热。

## 数据模型

- `images`:framework/version/python/cuda/image_ref、`prewarm_enabled`
- `image_node_cache`:image_id(FK CASCADE)、node_name、status(pending/pulling/cached/failed)、cached_ref、last_error、checked_at,Unique(image_id, node_name)

## 契约

| 端点                                           | 角色/鉴权    | 说明                                                                     |
| ---------------------------------------------- | ------------ | ------------------------------------------------------------------------ |
| `GET /api/admin/v1/images`                     | ops/readonly | 列表 + `coverage{cached,total,pct}` + `failed_nodes`,纯 DB 聚合          |
| `POST /api/admin/v1/images`                    | ops          | 创建;image_ref 冲突 409;审计                                             |
| `PATCH /api/admin/v1/images/{image_id}`        | ops          | 含 prewarm_enabled;image_ref 变更同事务清该镜像 cache 行;审计            |
| `DELETE /api/admin/v1/images/{image_id}`       | ops          | reason 必填;cache 行 CASCADE;审计                                        |
| `POST /api/admin/v1/images/{image_id}/prewarm` | ops          | 非 cached 行置 pending + 同事务 enqueue,返回 `{enqueued}`;请求路径零 K8s |
| `GET /api/admin/v1/images/{image_id}/nodes`    | ops/readonly | 每节点 status/last_error/checked_at                                      |

## 默认镜像目录(平台自带)

平台自带 12 个镜像:PyTorch / TensorFlow / Miniconda / PaddlePaddle 各自的 CUDA 线,外加 CPU 向 DataScience(R + Julia + scipy)。
镜像矩阵、选版规则、逐镜像 tag、必装 Jupyter 套件、构建命令、推送前自检与取 digest 只写在 `deploy/instance-images/README.md` 一处;`apps/api/scripts/seed_dev.py` 的 `IMAGES` 是同一张表的 dev 种子副本(两处同一提交改)。

## 规则与不变量

- 公开 `GET /api/v1/images` 的 `is_prewarmed` 是计算值:`prewarm_enabled AND`(无 cache 行 → 等于 prewarm_enabled;有行 → coverage ≥ `prewarm_min_coverage_pct`)。
- 策略参数(ops 可调):`prewarm_min_coverage_pct` 与 `prewarm_recheck_hours`,默认值见 [limits.md](./limits.md)。
- 预热由 `image.prewarm` outbox handler(幂等)+ `prewarm_patrol` 巡检(60s,advisory lock 1008)铺行、收敛与复检;节点增删由巡检自行发现。执行体是每节点定点 Job,与 `disk.wipe` 同构。
- cpu 池不预热(巡检 `target_nodes` 排除 `pool_label == cpu`):CPU 规格首次启动现拉镜像,创建页对它不承诺秒级启动。
- 删除镜像不影响运行中实例:实例存 image_ref 快照。
- 实例的 image_ref 快照终身不变:停机/开机/重启都用它,无「实例换镜像」端点;镜像修复只对新建实例生效。
- 集群内 P2P 缓存用发行版内置 embedded registry mirror(Spegel);`latest` tag 不参与 P2P,平台镜像一律钉版本 tag。
- 平台镜像 tag 允许同名重推,目录 `image_ref` 必须钉 digest。重推后把管理端该镜像 ref 换成新 digest:`admin_update_image` 同事务清 cache 行,巡检按新 ref 重新预热;绕过服务层直接改库由巡检比对 `cached_ref` 兜底作废,≤60s 自愈。
- 平台镜像仓是 Harbor(接入参数在平台配置·镜像仓库组,见 [platform-config.md](./platform-config.md)):`image_ref` 一律全限定名 `<host>/<项目>/<名>:<tag>@sha256:<digest>`。形态校验事实源 `core/registry.is_valid_image_ref`,创建实例与管理端目录 CRUD 共用。
- 拉取凭据由平台托管:worker 在建实例 Pod / 预热 Job 前按生效配置把 `superdl-registry-pull`(`kubernetes.io/dockerconfigjson`)按指纹写入 superdl 与租户 ns(`core/k8s.ensure_registry_pull_secret` → `ensure_pull_secret`),Pod / Job 以 `imagePullSecrets` 引用;未配机器人账户则不生成。轮换 = 配置中心保存新 Secret。节点 registries.yaml 只承担 Spegel P2P / Harbor CA / 代理缓存 mirror,见 [nodes.md](./nodes.md);发布 SOP 见 `deploy/cluster/runbooks/image-prewarm.md`。
- 创建实例的镜像形态校验与来源白名单见 [security.md](./security.md)。
