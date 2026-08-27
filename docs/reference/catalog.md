# 商品目录

SKU 管理(管理端 CRUD)、用户端市场查询、平台镜像目录与近似库存。

## 数据模型

- `skus`:name、gpu_model、tier(dedicated / shared)、mig_profile?、gpu_cores_pct、vram_gb、oversell_cores numeric(4,2)、oversell_vram numeric(4,2)、pool_label、vcpu、mem_gb、disk_gb(含 100G 实例盘)、price_hourly numeric(12,4)、max_gpus_per_instance、cuda_max、status(on/off)
- `images`:平台镜像树 framework→version→python→cuda→image_ref、prewarm_enabled;预热见 [images.md](./images.md)

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/skus?tier=&gpu_model=` | 匿名 | 仅 on 架;含 available_count(每请求按节点台账直接算,全部 SKU 批量一次) |
| `GET /api/v1/images` | 匿名 | 平台镜像目录;`is_prewarmed` 为计算值 |
| `GET /api/admin/v1/skus` | ops/finance/readonly | SkuAdminOut 含 `capacity_gpus / sold_share / actual_oversell` |
| `POST /api/admin/v1/skus` | ops | 创建 |
| `PATCH /api/admin/v1/skus/{sku_id}?force=` | ops | 可改 `pool_label` + `mig_profile`(成对,仅下架态;在售改任一个 409 `CONFLICT`)。撞业务唯一键 409 `skuBusinessKeyExists`。`status→on` 时硬校验「台账存在 model_matches 且 pool 相符的 Ready 节点」,失败 409 `SKU_NOT_SELLABLE`(报错指明缺哪种型号×池),force 跳过 |
| `GET /api/admin/v1/skus/capacity-preview` | ops/readonly | query `gpu_model&pool_label&gpu_cores_pct&oversell_cores&vram_gb` → `{matching_nodes, ready_gpus, total_gpus, est_instances, warnings[]}`;纯 DB,hami 池 est = ready_gpus × ⌊100×oversell/pct⌋(折算口径只看池,不收 tier) |

`capacity_gpus / sold_share / actual_oversell` 由 adminapi 组装 catalog+nodes+orchestrator 三个 service 得出,catalog 不反向依赖它们。

## 规则与不变量

- SKU 变更只影响新实例:实例落库时快照 `price_hourly` 与规格,存量实例不随改价变动。
- SKU 业务唯一键 `(gpu_model, tier, pool_label, mig_profile, gpu_cores_pct, vcpu, mem_gb)` 唯一索引(NULLS NOT DISTINCT,mig_profile 为 NULL 也判重),重复创建 409。带 pool_label 是因为「共享」既可能落 mig 池也可能落 hami 池;带 vcpu/mem_gb 是为了同一张卡能出不同配套规格。
- 近似库存按 (池, canonical 型号) 双维度估:数据源是节点台账 `node_specs`(巡检 60s 写,只算 Ready 节点空闲卡),请求路径不直连 K8s;provider 一次批量计算全部 SKU,每请求直接算、不缓存(台账几十行,一次 SELECT + Python 循环)。
- 创建路径软准入:台账明确该 (池,型号) 可分配量不足 → 409 `NO_CAPACITY`,台账无数据一律放行。不做库存预占,库存是近似值(台账 60s 巡检粒度),最终以调度结果为准。
- hami 池每卡可售实例数 = ⌊100 × oversell_cores ÷ gpu_cores_pct⌋,只在 `catalog/service.py::sellable_per_gpu` 一处按 Decimal 整除(kata / mig 池恒 1);(池, canonical 型号) 匹配只在 `nodes/service.py::matching_specs` 一处。市场库存、创建软准入、管理端容量列与容量预览共用这两份口径,不得各算各的。
- 超卖参数是纯定价参数,不下发调度;显存超卖 >1.2 由前端二次确认。
- 上架为硬校验(可 force 覆盖),创建与编辑为软校验(容量预览警示,可保存)。
- off 架 SKU 用户端不可见;readonly 角色全站只读,finance 不能改 SKU。
- **档位是售卖分类,隔离机制的事实源是 `pool_label`。** `tier` 只有 dedicated / shared 两值;用户看到的「共享·标准 / 共享·经济」由池派生(mig 池 = 硬切分标准档,hami 池 = 软切分经济档),前端在 `packages/ui/src/status.ts::skuVariant` 一处映射。
- **在售规格不能改池、也不能改 MIG 切片。** 两者决定隔离方式与用户看到的规格(档位徽标与规格列),在售改动会让市场页挂着的「共享·标准」静默变成「共享·经济」、或切片悄悄换掉,新下单的人拿到的不是他看到的那件商品(存量实例走 spec 快照,不受影响)。要改先下架,或新建规格;`force` 也不放行——这不是容量问题。
- 档位与池必须配对(`dedicated⇔kata`、`shared⇔mig|hami`),mig 切片与 mig 池同时有或同时无:建 SKU 与改池两条路径共用 `catalog/service.py::_check_tier_pool`,配对表在 `core/gpu_adapter.TIER_POOLS`。管理端表单只让运营选展示档位,tier 与 pool_label 由它派生,不给各改各的机会。
- SKU name 结构化(gpu_model + 档位拼展示名),DB 不存中文名。
- 从集群资源创建 SKU 时型号下拉取自节点台账,见 [nodes.md](./nodes.md)。
