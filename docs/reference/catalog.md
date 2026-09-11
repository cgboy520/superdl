# 商品目录

SKU 管理(管理端 CRUD)、用户端市场查询、平台镜像目录与近似库存。

## 数据模型

- `skus`:name、gpu_model、tier(dedicated / shared / cpu)、mig_profile?、gpu_cores_pct、vram_gb、oversell_cores numeric(4,2)、pool_label、vcpu、mem_gb、disk_gb(含 100G 实例盘)、price_hourly numeric(12,4)、max_gpus_per_instance、cuda_max、period_enabled(默认 true)、spot_enabled(默认 false)、status(on/off)
- CPU 规格(tier=cpu)字段约定:`gpu_model=""`、`gpu_cores_pct=0`、`vram_gb=0`、`mig_profile=NULL`、`max_gpus_per_instance=0`,`price_hourly` 是整机时价(GPU 规格是单卡时价)。跨字段规则在 `catalog/schemas.py::cpu_spec_error` 一处:建 SKU 由 `SkuCreate` model_validator 调用(422),改 SKU 由 `service.admin_update_sku` 合并终态后调用(400 + `message_key`)。GPU 规格的这三项不许为 0。
- `images`:framework→version→python→cuda→image_ref、prewarm_enabled;预热见 [images.md](./images.md)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/skus?tier=&gpu_model=` | 匿名 | 仅 on 架;含 available_count(每请求按节点台账算,全部 SKU 批量一次)、`period_enabled`、`spot_enabled` |
| `GET /api/v1/images` | 匿名 | 平台镜像目录;`is_prewarmed` 为计算值 |
| `GET /api/admin/v1/skus` | ops/finance/readonly | SkuAdminOut 含 `capacity_gpus / sold_share / actual_oversell` |
| `POST /api/admin/v1/skus` | ops | 创建;`period_enabled` 省略即 true;`spot_enabled` 省略即 false |
| `PATCH /api/admin/v1/skus/{sku_id}?force=` | ops | 可改 `pool_label` + `mig_profile`(成对,仅下架态;在售改任一 409 `CONFLICT`)。撞业务唯一键 409 `skuBusinessKeyExists`。`status→on` 硬校验「台账存在 model_matches 且 pool 相符的 Ready 节点」,失败 409 `SKU_NOT_SELLABLE`,force 跳过 |
| `GET /api/admin/v1/skus/capacity-preview` | ops/readonly | query `pool_label&gpu_model&gpu_cores_pct&oversell_cores&vram_gb&vcpu&mem_gb` → `{matching_nodes, ready_gpus, total_gpus, est_instances, warnings[]}`;纯 DB,hami 池 est = ready_gpus × ⌊100×oversell/pct⌋。`gpu_model` 留空 = CPU 规格预览:只按池匹配节点,`ready_gpus/total_gpus` 恒 0,est 走 `sellable_cpu_slots` |

`capacity_gpus / sold_share / actual_oversell` 由 adminapi 组装 catalog+nodes+orchestrator 三个 service 得出,catalog 不反向依赖。

## 规则与不变量

- SKU 变更只影响新实例:实例落库时快照 `price_hourly` 与规格。
- SKU 业务唯一键 `(gpu_model, tier, pool_label, mig_profile, gpu_cores_pct, vcpu, mem_gb)` 唯一索引(NULLS NOT DISTINCT),重复创建 409。
- 近似库存按 (池, canonical 型号) 双维度估:数据源是 `node_specs`(巡检 60s 写,只算 Ready 节点空闲卡),请求路径不直连 K8s;每请求直接算、不缓存。
- 创建路径软准入:台账明确该 (池,型号) 可分配量不足 → 409 `NO_CAPACITY`,台账无数据放行。不做库存预占,最终以调度结果为准。
- hami 池每卡可售实例数 = ⌊100 × oversell_cores ÷ gpu_cores_pct⌋,只在 `catalog/service.py::sellable_per_gpu` 一处按 Decimal 整除(kata / mig 恒 1);(池, canonical 型号) 匹配只在 `nodes/service.py::matching_specs` 一处。市场库存、软准入、管理端容量列与容量预览共用这两份口径。
- CPU 规格库存口径在 `catalog/service.py::sellable_cpu_slots` 一处:逐 Ready 节点取 `min(⌊预算 vCPU ÷ sku.vcpu⌋, ⌊预算内存 ÷ sku.mem_gb⌋)` 求和。cpu 池整机 vCPU/内存都算;hami 池每节点封顶策略 `gpu_node_cpu_instance_vcpu_cap` 核、内存同比例折算,cap=0 即该节点一台不卖。这是上限估算而非实时余量。
- CPU 规格不按型号匹配节点,只按池(`nodes/service.py::pool_specs`)。上架硬校验只校验「池里有 Ready 节点」,报 `catalog.skuNotSellableCpu`;软准入不足报 `orchestrator.noCapacityCpu`。
- 管理端 SKU 列表的 `capacity_gpus / sold_share / actual_oversell` 对 CPU 规格渲染「—」。
- `period_enabled`(默认 true)与 `spot_enabled`(默认 false)决定这条规格接不接受包周期下单、上不上竞价档,与 `tier` / `pool_label` 无关。为假时市场页对应 chip 置灰,服务端在 `orchestrator.create_instance` 兜住直调(`orchestrator.periodNotEnabled` / `orchestrator.spotNotEnabled`)。
- 改这两个开关只影响新单。已售出的订阅照常到期、按 `subscriptions.unit_price` 原价快照续费;已在跑的竞价实例照常按快照价计费、照常可能被回收(免除回收风险须用户走 `/to-on-demand`,见 [orchestrator.md](./orchestrator.md))。口径见 [billing.md](./billing.md)。
- 超卖参数是纯定价参数,不下发调度。
- 上架为硬校验(可 force),创建与编辑为软校验(容量预览警示,可保存)。
- off 架 SKU 用户端不可见;readonly 全站只读,finance 不能改 SKU。
- 档位是售卖分类,隔离机制的事实源是 `pool_label`。`tier` 只有 dedicated / shared / cpu;「共享·标准 / 共享·经济」由池派生(mig = 硬切分标准档,hami = 软切分经济档),前端在 `packages/ui/src/status.ts::skuVariant` 一处映射。cpu 档不按池分化展示。
- mig 池是硬件强制隔离,hami 池是 CUDA 层软件限额(容器内 root 可绕过):hami 池只能用于性能隔离 / 成本优化。分级表见 [security.md](./security.md)。
- 在售规格不能改池、也不能改 MIG 切片;要改先下架或新建规格,`force` 不放行。
- 档位与池必须配对(`dedicated⇔kata`、`shared⇔mig|hami`、`cpu⇔cpu|hami`),mig 切片与 mig 池同时有或同时无:建 SKU 与改池共用 `catalog/service.py::_check_tier_pool`,配对表在 `core/gpu_adapter.TIER_POOLS`。共享档再叠加运营开关 `SUPERDL_SHARED_TIER_ALLOWED_POOLS`(默认 `mig,hami`;摘掉 hami 即「共享档只卖 MIG」,置空停售共享档)。管理端表单只让运营选展示档位,tier 与 pool_label 派生。
- SKU name 结构化(gpu_model + 档位),DB 不存中文名。
- 从集群资源创建 SKU 时型号下拉取自节点台账,见 [nodes.md](./nodes.md)。
