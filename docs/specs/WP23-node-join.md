# WP23 · GPU 服务器一键加入集群(2026-08-20)

方向决策经人工确认:
1. 主路线 = **管理端生成一次性注册命令**(Rancher/GitLab Runner 范式):选池 → 生成 `curl … | sudo bash -s -- --token sdln_xxx` → 新服务器粘贴执行 → 管理端实时看进度。「纯一键」(平台 SSH 直连装机)后置:平台持全部服务器 root 凭据 = 被攻破即全节点 root,且未来补上时可完全复用本 WP 链路(远程替你粘贴同一条命令)。
2. **静态脚本 + token 走命令行参数**:脚本本体入库受版本控制(可 shellcheck/bats),内容零密钥可公开缓存;token 不进 URL(不落 access log/代理日志)。RKE2 server URL/join token **不进脚本**——脚本凭 token `POST /bootstrap` 换取(王冠资产只走 Bearer POST 响应体)。
3. RKE2 server URL / join token / 版本 / 驱动版本 / registries.yaml 内容 → platform-config 新增 `cluster` 组(token 用 `kind="secret"` AES-GCM),仅 admin 可读写;ops 生成注册命令时服务端代读,永不见明文。

## 目标

- 后端:新模块 `app/modules/nodes/`——`node_enrollments` 表 + 状态机(transition 集中,对齐铁律 #10 精神)+ 令牌(`sdln_` 前缀 256-bit,只存 sha256)+ 管理端 4 端点 + 匿名侧 3 端点(script/bootstrap/progress,token 即鉴权,先例 webhooks)+ 30s 对账器(K8s 真出现 Ready 且池标签匹配才判 joined,advisory lock 1009)。
- 脚本:`apps/api/app/modules/nodes/assets/node-join.sh`(随 API 镜像打包下发)把 ansible 基线 + agent-config + README 人工步骤产品化为幂等 bash(步骤 marker 可无限重跑;kata 池 IOMMU 等需重启场景用 systemd oneshot 断点续跑);CI 加 shellcheck + bats。
- 管理端:nodes.tsx 增「添加节点」Modal(命令只显示一次)+「待加入节点」进度卡(5s 轮询);顺带补齐 cordon/uncordon(走 outbox,RBAC 扩 nodes patch)。

## 契约

| 端点 | 鉴权/角色 | 说明 |
|---|---|---|
| `GET /api/v1/node-enroll/script` | 匿名+限流 | 静态脚本,仅替换 `__API_BASE__`;内容零密钥 |
| `POST /api/v1/node-enroll/bootstrap` | Bearer token | 上报 hostname/os/gpu → 回 pool/rke2 版本/server_url/join_token/驱动版本/nvme/registries_yaml;仅 pending/installing/rebooting 放行(支持重跑/重启续跑),其余**统一 404**(防探测);按 IP 限流 |
| `POST /api/v1/node-enroll/progress` | Bearer token | `{phase, state: running\|ok\|failed\|rebooting, message?}` 推进 phase/status/error/心跳 |
| `GET /api/admin/v1/node-enrollments` | ops, readonly | 列表,`?active=true` 过滤(排除 revoked/超 24h 的 joined/超 7d 的 expired) |
| `POST /api/admin/v1/node-enrollments` | ops | 创建;**响应含 token 明文与完整命令,仅此一次**;Idempotency-Key 重放不建新行(轮换该行 token 后返回);cluster 组未配 server_url/join_token → 409 引导 |
| `POST .../{id}/regenerate` | ops | 仅 pending/expired/failed:换新 token/有效期,状态回 pending |
| `POST .../{id}/revoke` | ops | reason 必填,非终态 → revoked |
| `POST /api/admin/v1/nodes/{name}/cordon` | ops | reason 必填,enqueue `node.cordon`(请求路径不动 K8s);uncordon 同 |

platform-config 新增 `cluster` 组:`rke2_server_url`(pattern `https://…:9345`)、`rke2_join_token`(secret)、`rke2_version`、`node_driver_version`、`node_registries_yaml`(text,即 WP22 registries.yaml 内容——两 WP 唯一接缝)。

## 数据变更

新表 `node_enrollments`(迁移 `wp23_node_enrollments`):`token_hash(sha256 唯一) / pool / hostname? / note? / nvme_devices JSONB? / status(pending→installing→rebooting→joining→joined | failed | expired | revoked) / phase / error / node_name / reported_ip / os_info JSONB / gpu_info JSONB / expires_at(默认24h) / last_report_at / joined_at / created_by / idempotency_key(与 created_by 联合唯一) / 时间戳`。

## 状态机与闭环

- 脚本各阶段回报:precheck/nouveau/sysctl/iommu/driver/nvme_vg/reboot/registries/rke2_config/rke2_install/rke2_start/waiting_node。
- `rke2_start ok` → joining;`rebooting` → rebooting(oneshot 续跑后重新 bootstrap → installing)。
- 对账器(30s):joining/installing/rebooting 且 K8s 有该 node_name 且 Ready 且 pool 标签匹配 → joined(令牌即死);池标签不符 → failed;pending 过 expires_at → expired;2h 无心跳 → failed。
- **无预热联动代码**:WP22 巡检 60s 自动发现新 Ready 节点并铺预热行,零耦合。

## 安全边界 / 明确不做

- 令牌 256-bit 熵 + 只存哈希 + 统一 404(无效/过期/吊销/终态不区分原因)+ `check_rate_limit` 按 IP 限流 + 硬过期(默认 24h,1~168h 可调)。
- join token 最终必然落节点 `/etc/rancher/rke2/config.yaml`(0600 root)——RKE2 架构决定;runbook 补 `rke2 token rotate` 轮换指引。
- **不做**:平台 SSH 直连装机(后置);批量可重用令牌(一节点一令牌,批量用连续创建);drain(驱逐牵扯计费/迁移策略,后置);bootstrap 上报的 hostname 与期望不符 → failed(防令牌串用)。

## 验收用例

1. ops 创建 → 响应含 token 明文与两种命令;列表接口不含 token;同 Idempotency-Key 重放不产生第二行。
2. 凭 token bootstrap → 拿到 server_url/join_token/pool,状态 pending→installing;无效/过期/吊销/joined 的 token 一律 404;高频 → 429。
3. cluster 组未配置时创建 → 409;readonly 创建 → 403;finance 列表 → 403。
4. progress rebooting/failed 正确落 status/phase/error;rke2_start ok → joining;终态后 progress → 404。
5. fake 注入 Ready 节点(池匹配)→ reconcile 置 joined;池不符 → failed;pending 过期 → expired;installing 2h 无心跳 → failed。
6. revoke 需 reason 且入审计(审计不落 token);regenerate 仅限 pending/expired/failed。
7. cordon:端点只入队,handler 幂等,重复 cordon 无副作用;readonly 403。
8. shellcheck 零告警;bats:幂等重跑/需重启矩阵/ERR 上报。
9. 后端全套门禁 + 前端四件套 + OpenAPI/orval 零 diff。

## 实机验证(留待集群)

脚本三池全流程(kata 重启续跑必测)、RKE2 真实 join、registries.yaml 生效、patch_node cordon、`cat /var/lib/rancher/rke2/server/node-token` 录入管理端的引导路径。
