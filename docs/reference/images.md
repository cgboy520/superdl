# 镜像与预热

平台镜像目录的管理端 CRUD、集群内 P2P 缓存与逐节点预热。

## 数据模型

- `images`:framework/version/python/cuda/image_ref、`prewarm_enabled`(管理员意图)
- `image_node_cache`:image_id(FK CASCADE)、node_name、status(pending/pulling/cached/failed)、last_error、checked_at,Unique(image_id, node_name)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/admin/v1/images` | ops/readonly | 列表 + `coverage{cached,total,pct}` + `failed_nodes`,纯 DB 聚合不调 K8s |
| `POST /api/admin/v1/images` | ops | 创建;image_ref 冲突 409;审计 |
| `PATCH /api/admin/v1/images/{image_id}` | ops | 含 prewarm_enabled;image_ref 变更同事务清该镜像 cache 行;审计 |
| `DELETE /api/admin/v1/images/{image_id}` | ops | reason 必填;cache 行 CASCADE;审计 |
| `POST /api/admin/v1/images/{image_id}/prewarm` | ops | 非 cached 行置 pending + 同事务 enqueue,返回 `{enqueued}`;请求路径零 K8s |
| `GET /api/admin/v1/images/{image_id}/nodes` | ops/readonly | 每节点明细 status/last_error/checked_at |

## 默认镜像目录(平台自带)

平台自带 11 个镜像:PyTorch / TensorFlow / Miniconda / PaddlePaddle × CUDA `13.2` `12.9` `11.8` 三条线。
**选版规则、逐镜像 tag、构建命令与推送前自检**只写在 `deploy/instance-images/README.md` 一处,以它为准;
`apps/api/scripts/seed_dev.py` 的 `IMAGES` 是同一张表的 dev 种子副本(改镜像矩阵两处同一提交一起改)。
规则要点:框架只上「最新稳定版 + 最后一个支持 CUDA 11.8 的稳定版」(不收 rc/beta),Python 取该框架
支持的最高版本,框架镜像以同 CUDA 线的 Miniconda 镜像为父镜像(conda / sshd / Jupyter 套件共用 layer)。

每个平台镜像必装的 Jupyter 套件(契约见同一份 README):`jupyterlab` / `jupyter-ai` /
`jupyter-resource-usage` / `jupyterlab-language-pack-zh-CN` / `ipykernel`;默认界面 `/lab`,界面语言默认 zh-CN。

## 规则与不变量

- 公开 `GET /api/v1/images` 的 `is_prewarmed` 是计算值:`prewarm_enabled AND`(无 cache 行 → 等于 prewarm_enabled;有行 → coverage ≥ `prewarm_min_coverage_pct`)。
- 策略参数(ops 可调):`prewarm_min_coverage_pct` 默认 90、`prewarm_recheck_hours` 默认 24。
- 预热由 `image.prewarm` outbox handler(幂等)+ `prewarm_patrol` 巡检(60s,advisory lock 1008)铺行、收敛与复检;节点增删由巡检自行发现,与装机链路零耦合。
- 预热执行体是每节点定点 Job,与 `disk.wipe` 同构,不扩 K8s RBAC。
- 删除镜像不影响运行中实例:实例存的是 image_ref 快照。
- 集群内 P2P 缓存用发行版内置 embedded registry mirror(Spegel);`latest` tag 不参与 P2P,故平台镜像一律钉版本 tag。
- **平台镜像的同名 tag 允许重推**(不要求 `-rN` 递增):实例 Pod 是 `imagePullPolicy=IfNotPresent`(开机不依赖仓库可达),
  预热 Job 是 `Always`,所以重推后点一次「立即预热」(或等 `prewarm_recheck_hours` 复检)即可把节点缓存刷到新 digest;
  已在跑的实例不受影响,重建后生效。构建与自检步骤见 `deploy/instance-images/README.md`。
- 平台镜像仓是 Harbor(接入参数在平台配置·镜像仓库组,见 [platform-config.md](./platform-config.md)):`image_ref` 一律存 Harbor 全限定名 `<host>/<项目>/<名>:<tag>`,没有逻辑名。拉取凭据由平台托管:worker 在建实例 Pod / 预热 Job 之前按生效配置把 `superdl-registry-pull`(`kubernetes.io/dockerconfigjson`)按指纹写入 superdl 与该租户 ns(`core/registry.ensure_registry_pull_secret` → `ensure_pull_secret`,指纹相同不覆写),Pod / Job 以 `imagePullSecrets` 引用;未配机器人账户(项目 public)则不生成、不引用。轮换 = 配置中心保存新 Secret,节点不落凭据。节点 registries.yaml 只承担 Spegel P2P / Harbor CA / 代理缓存 mirror,见 [nodes.md](./nodes.md);发布 SOP 见 `deploy/cluster/runbooks/image-prewarm.md`。
- 创建实例的镜像形态校验与来源白名单见 [security.md](./security.md)。
