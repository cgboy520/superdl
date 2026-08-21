# 商品目录

SKU 管理(管理端 CRUD)、用户端市场查询、平台镜像目录与近似库存。

## 数据模型

- `skus`:name、gpu_model、tier(dedicated/mig/shared_std/shared_eco)、mig_profile?、gpu_cores_pct、vram_gb、oversell_cores numeric(4,2)、oversell_vram numeric(4,2)、pool_label、vcpu、mem_gb、disk_gb(含 100G 实例盘)、price_hourly numeric(12,4)、max_gpus_per_instance、cuda_max、status(on/off)
- `images`:平台镜像树 framework→version→python→cuda→image_ref、prewarm_enabled;预热见 [images.md](./images.md)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/skus?tier=&gpu_model=` | 匿名 | 仅 on 架;含 available_count(30s 进程内缓存) |
| `GET /api/v1/images` | 匿名 | 平台镜像目录;`is_prewarmed` 为计算值 |
| `GET /api/admin/v1/skus` | ops/finance/readonly | SkuAdminOut 含 `capacity_gpus / sold_share / actual_oversell` |
| `POST /api/admin/v1/skus` | ops | 创建 |
| `PATCH /api/admin/v1/skus/{sku_id}?force=` | ops | `status→on` 时硬校验「台账存在 model_matches 且 pool 相符的 Ready 节点」,失败 409 `SKU_NOT_SELLABLE`(报错指明缺哪种型号×池),force 跳过 |
| `GET /api/admin/v1/skus/capacity-preview` | ops/readonly | query `gpu_model&pool_label&tier&gpu_cores_pct&oversell_cores` → `{matching_nodes, ready_gpus, total_gpus, est_instances, warnings[]}`;纯 DB,共享档 est = ready_gpus × ⌊100×oversell/pct⌋ |

`capacity_gpus / sold_share / actual_oversell` 由 adminapi 组装 catalog+nodes+orchestrator 三个 service 得出,catalog 不反向依赖它们。

## 规则与不变量

- SKU 变更只影响新实例:实例落库时快照 `price_hourly` 与规格,存量实例不随改价变动。
- 不做库存预占:库存是 30s 缓存的近似值,最终以调度结果为准。
- 超卖参数是纯定价参数,不下发调度;显存超卖 >1.2 由前端二次确认。
- 上架为硬校验(可 force 覆盖),创建与编辑为软校验(容量预览警示,可保存)。
- off 架 SKU 用户端不可见;readonly 角色全站只读,finance 不能改 SKU。
- SKU name 结构化(gpu_model + tier 拼展示名),DB 不存中文名。
- 从集群资源创建 SKU 时型号下拉取自节点台账,见 [nodes.md](./nodes.md)。
