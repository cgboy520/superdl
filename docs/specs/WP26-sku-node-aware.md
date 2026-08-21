# WP26 · SKU 节点感知(台账 · 型号约束调度 · 从集群生成)

方向决策经人工确认:
1. 现状是正确性缺口而非表单体验:**`gpu_model` 完全不参与调度**(gpu_adapter 仅认 pool_label,标 H100 的 SKU 可落到 4090 无感);无节点资源台账(enrollment.gpu_info 装机一次性、GFD 未启用、型号五种格式零归一化);SKU 创建/上架零集群校验。
2. **节点规格台账 + 60s 巡检**(仿 WP22 image_node_cache + prewarm_patrol 范式);**型号归一化 canonical 由平台回写节点 label `superdl.io/gpu-model`**(自有标签:不依赖 GFD、发行版无关,GFD 未来启用只是多一路数据源);调度约束走该 label 的 nodeSelector(全档位)。
3. **上架(status→on)硬校验**,`force` 可覆盖——**推翻 WP02:15「后端仅提示不拦截」先例**:上架直接决定用户可购,调度不到的 SKU 是资损与口碑事故,不是风格问题。创建仍软校验(容量预览接口反馈警示,可保存)。
4. 保留既有决策:**不做库存预占**,近似库存 30s 缓存 + 以调度结果为准(development-plan:66);超卖参数仍为纯定价参数不下发。
5. HAMi `nvidia.com/use-gputype` annotation **默认关**(HAMi 登记近原文串,与 canonical 不同构;启用需注 raw,实机验证项);主机制是自有 label。

## 目标

- **台账**:`node_specs` 表 + `nodes/patrol.py` 60s 巡检(advisory lock 1010):阶段 A 纯 K8s 读(list_nodes 全量含未打标 + enrollment 兜底)→ 阶段 B 单事务 DB 收敛 → 阶段 C 逐节点 label patch(失败下轮自愈)。节点消失先置 `Missing`、超 7 天删行(上架校验只认 Ready);**未打池标签节点入台账并标异常**(解决管理端不可见)。巡检不走 outbox:worker 收敛环既有范式(reconciler 直删 Pod / prewarm 直删 Job),铁律 #3 管请求路径业务事务原子性,巡检以幂等重试为原子性。
- **归一化**:`core/gpu_models.py` 纯函数 `canonical_gpu_model(raw)`(lspci 剥壳 → 分隔统一 → 剥噪声/形态 token → 规则表 → OVERRIDES 兜底;同名多容量家族 A100/A800/H100/H800/H200/V100 追加 `-{n}G`;未识别返回 None)+ `model_matches(sku_c, node_c)`(相等或 node 前缀匹配:SKU `A100` 匹配台账 `A100-80G`)+ `DEFAULT_VRAM_GB` 兜底表;规则/兜底表穷举单测。
- **调度**:gpu_adapter 扩 `gpu_model`(canonical → nodeSelector 全档位)+ `annotations` 字段(use-gputype 透传口,settings 开关默认关);spec 快照增 `gpu_model_selector` 键——**存量实例无此键天然不受影响**;InstancePodSpec 增 annotations,real 落 Pod metadata。
- **bootstrap 扩展**:node-join.sh 采集 `nvidia-smi --query-gpu=name,memory.total` → payload 增 `gpu_details:[{name, memory_mib}]`(旧 `gpus` 字段保留照发,契约兼容);多卡/单卡显存入台账。
- **管理端**:节点页改读台账(增 raw/unlabeled/label_synced/last_seen/vram 列,未识别型号黄牌);SKU 表单改「从集群资源创建」:型号下拉 = 台账聚合(型号/池/总卡/空闲/单卡显存),选后自动带 vram 上限、按整机配比推荐 vcpu/mem、tier↔pool_label 联动锁定、mig 必填 profile;右侧**容量预览**实时反馈;SKU 列表补 容量/已售/实际超卖率 列。
- 顺手:seed SKU name 结构化(gpu_model + tier 经 skuTierMap 拼展示名,消除 DB 中文名 i18n 债)。

## 契约

| 端点 | 角色 | 说明 |
|---|---|---|
| `GET /api/admin/v1/cluster/gpu-models` | ops/readonly | 台账聚合:`[{gpu_model, gpu_model_raw, pool_label, node_count, gpu_total, ready_gpu_total, vram_gb}]`(canonical×pool 分组;未识别入 `unrecognized` 桶) |
| `GET /api/admin/v1/skus/capacity-preview` | ops/readonly | query `gpu_model&pool_label&tier&gpu_cores_pct&oversell_cores` → `{matching_nodes, ready_gpus, total_gpus, est_instances, warnings[]}`(纯 DB;est 共享档 = ready_gpus × ⌊100×oversell/pct⌋) |
| `GET /api/admin/v1/nodes`(改造) | 不变 | 数据源切台账;NodeOut 增 `gpu_model_raw, unlabeled, label_synced, last_seen, vram_gb`;含未打标/Missing |
| `PATCH /api/admin/v1/skus/{id}`(改造) | 不变 | 新增 query `force: bool=false`;`status→on` 时硬校验「台账存在 model_matches 且 pool 相符的 Ready 节点」,失败 409 `SKU_NOT_SELLABLE`(文案指明缺哪种节点),force 跳过 |
| SKU 列表(改造) | 不变 | SkuAdminOut 增 `capacity_gpus / sold_share / actual_oversell`(adminapi 组装 catalog+nodes+orchestrator 三 service,避免 catalog 反向依赖) |

新 ErrorCode:`SKU_NOT_SELLABLE`。模块边界:catalog.service → nodes.service(上架校验)、orchestrator.service → nodes.service 均合规,**import-linter 九条契约零变更**(已对照 pyproject 核实)。

## 数据变更

新表 `node_specs`(迁移 `wp26_node_specs`):`node_name(唯一) / pool_label? / unlabeled / gpu_model_raw? / gpu_model?(canonical, index) / label_synced / gpu_count / gpu_used / vram_gb / vcpu / mem_gb / disk_gb / driver_version? / cuda_version? / status(Ready|NotReady|Cordoned|Missing, index) / last_seen / created_at / updated_at`。

`core/k8s/base.py` Protocol 扩:`list_nodes(include_unlabeled=False)`、`set_node_labels(node, labels)`(RBAC nodes patch 已有);NodeInfo 增 `gpu_model_label / model_label_current`。

## 分批(9 批)

归一化纯函数 → 台账表+读接口 → K8s Protocol 扩展(real/fake)→ 巡检+worker 注册+fake 用例 → bootstrap 全卡清单+bats → 管理端契约(nodes 切台账+gpu-models)→ gpu_adapter+快照+annotations → 上架校验+容量预览+SKU 列表组装 → SKU 表单前端。

## 验收用例

1. 归一化:五种来源格式(手填/nvidia-smi/lspci/GFD/fake)穷举 → canonical 正确;未知串 → None;model_matches 前缀语义正确。
2. 巡检(fake 注入):两轮收敛台账与集群一致;节点消失 → Missing → 超 7 天删行;未打标节点入账 unlabeled=true;label patch 失败下轮重试(label_synced=false 可见)。
3. 上架:集群不存在型号的 SKU status→on → 409 SKU_NOT_SELLABLE 且报错指明「型号 × 池」;force=true 通过;台账有匹配 Ready 节点 → 通过。
4. 调度:下单后 Pod nodeSelector 含 `superdl.io/gpu-model`(fake 断言);存量实例(快照无 gpu_model_selector)start 不带该 selector。
5. 容量预览:数字与台账一致;vram 超单卡上限 → warnings 含明确提示。
6. bootstrap:gpu_details 入库;旧客户端(仅 gpus 字段)兼容。

## 实机验证项(人工事项)

`superdl.io/gpu-model` nodeSelector 三档位真实调度命中(含混布池);use-gputype 开启时以 raw 注入的匹配语义;GFD 启用后数据源优先级共存(nvidia-smi > GFD label > 存量)。
