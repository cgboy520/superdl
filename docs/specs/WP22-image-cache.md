# WP22 · Docker 镜像本地缓存与预热产品化(2026-08-20)

方向决策经人工确认:
1. 集群内 P2P 缓存用 RKE2 内置 embedded registry mirror(Spegel),零新增组件;dragonfly/nydus/stargz 对本规模过重,否决。
2. 私有仓库本轮落地:单实例 distribution registry(哑仓库,无 UI 无扫描),让被 seed/e2e/ansible 全面引用却不存在的 `registry.superdl.local` 成真;**不算提前触发 Harbor 后置项**(Harbor 的价值是多租户/扫描/复制,未来落地时镜像 `skopeo copy` 直迁)。
3. 预热产品化:管理端镜像 CRUD + `image.prewarm` outbox 任务 + 每节点定点 Job(与 `disk.wipe` 同构,K8s RBAC 零扩权)+ worker 巡检收敛;`is_prewarmed` 从静态标记演进为真实计算状态。

## 目标

- 部署:RKE2 server 开 `embedded-registry: true`;全节点分发 `registries.yaml`(`"*"` 通配 mirror 参与 P2P + `registry.superdl.local` endpoint 指向集群内 registry NodePort);registry 原生清单(`registry:3` 钉版本 + TopoLVM PVC + NodePort 30500);ansible 删除失效的 `crictl pull` 预热 task(首装必失败且注释里的 cron 不存在),预热职责移交平台。
- 后端:`images.is_prewarmed` 改名 `prewarm_enabled`(管理员意图);新表 `image_node_cache` 记录每镜像×每节点缓存状态;`image.prewarm` outbox handler(幂等)+ `prewarm_patrol` 巡检(60s,advisory lock 1008)铺行/收敛/复检;`K8sOrchestrator` Protocol 扩 `prewarm_image / get_prewarm_status / delete_prewarm_job`(real=定点 Job,fake=内存模拟)。
- 管理端:新页面「镜像与预热」(CRUD、预热开关、每节点覆盖率、失败明细与重试)。

## 契约(新增 6 端点 + 1 语义演进)

| 端点 | 模块 | 说明 |
|---|---|---|
| `GET /api/admin/v1/images` | adminapi→catalog | 列表 + coverage{cached,total,pct} + failed_nodes(纯 DB 聚合,不调 K8s);ops/readonly |
| `POST /api/admin/v1/images` | 同上 | 创建;image_ref unique 冲突 409;ops;审计 |
| `PATCH /api/admin/v1/images/{id}` | 同上 | 更新(含 prewarm_enabled);image_ref 变更同事务清该镜像 cache 行;ops;审计 |
| `DELETE /api/admin/v1/images/{id}` | 同上 | reason 必填;cache 行 CASCADE;运行中实例存 image_ref 快照不受影响;ops;审计 |
| `POST /api/admin/v1/images/{id}/prewarm` | 同上 | 非 cached 行置 pending + 同事务 enqueue,返回 {enqueued};请求路径零 K8s;ops;审计 |
| `GET /api/admin/v1/images/{id}/nodes` | 同上 | 每节点明细(status/last_error/checked_at);ops/readonly |

公开 `GET /api/v1/images` **形状不变**,`is_prewarmed` 变计算值:`prewarm_enabled AND(无 cache 行→回落旧语义 / 有行→coverage ≥ prewarm_min_coverage_pct)`。策略参数(policies,ops 可调):`prewarm_min_coverage_pct` 默认 90、`prewarm_recheck_hours` 默认 24。

## 数据变更

- `images.is_prewarmed` → 改名 `prewarm_enabled`(迁移 `wp22_image_prewarm`)。
- 新表 `image_node_cache`:`image_id FK CASCADE / node_name / status(pending|pulling|cached|failed) / last_error / checked_at / created_at / updated_at`,Unique(image_id, node_name)。

## 安全边界 / 明确不做

- **不做** create_instance 目录白名单校验(ui-ux-spec §3.3 明确支持「自定义镜像」自由输入)。
- registry 为集群内网明文 HTTP(NodePort 30500),防火墙不得对外暴露;无认证(匿名 pull,push 走内网运维通道)——TLS/认证随 Harbor 后置项评估。
- Spegel 限制:`latest` tag 不参与 P2P(平台镜像一律钉版本 tag)。
- 巡检/handler 不在 API 请求路径调 K8s;enqueue 与 cache 行写入同事务(硬规范 #3)。

## 验收用例

1. ops POST /images → 201 且 audit_log 有 target;重复 image_ref → 409 CONFLICT;readonly 写 → 403;finance 读 → 403。
2. 全链路(Fake):建镜像 → patrol 铺行(=Ready 节点数,pending)→ outbox drain 置 pulling → patrol 收敛 cached → `GET /api/v1/images` is_prewarmed=true。
3. 失败与阈值:注入一节点 failed → coverage < 90% → is_prewarmed=false;admin 列表 failed_nodes=1、last_error 透出。
4. 幂等:同一 image.prewarm handler 执行两次 → Job 唯一、行不重复。
5. 节点伸缩:注入新节点 → patrol 补行+enqueue;节点消失 → 行删除。
6. 复检:cached 行 checked_at 超 recheck 窗口 → 回 pending 重新 enqueue。
7. 兼容回落:零 cache 行时 is_prewarmed == prewarm_enabled(dev/e2e 零回归)。
8. PATCH prewarm_enabled=false → patrol 后该镜像 cache 行清零,is_prewarmed=false。
9. menu 单测:MENU_ROLES["/images"]=["admin","ops","readonly"]。
10. 后端 ruff/pyright/pytest(billing≥90%)/import-linter/alembic check 全绿;前端四件套全绿;OpenAPI 与 orval 产物零 diff。

## 实机验证(本地无真实 K8s,留待集群)

Spegel 节点间 P2P 秒拉;registry NodePort push/pull;prewarm Job 在 kata/hami/mig 三池均可落(tolerations Exists);kubelet 镜像 GC 后复检重拉;20GB 级镜像 activeDeadlineSeconds=1800 是否充足。

## 附录 A · ui-ux-spec.md 对应修订(禁改文档,仅在此记录,沿 WP20 先例)

- §4.1 管理端信息架构:新增入口「镜像与预热」(admin/ops 可写,readonly 可见)。
- §3.3 创建页「预热镜像,秒级启动」标注自 WP22 起有真实数据支撑(is_prewarmed 为覆盖率计算值)。
