# WP2 · 商品

> ✅ 已交付并通过验收用例(tests/test_catalog.py)。

## 目标
SKU 管理(admin CRUD)+ 用户端市场查询 + 平台镜像目录 + 近似库存。

## 数据
- `skus`:name、gpu_model、tier(dedicated/mig/shared_std/shared_eco)、mig_profile?、gpu_cores_pct、vram_gb、oversell_cores numeric(4,2)、oversell_vram numeric(4,2)(≤1.2 需 UI 二次确认)、pool_label、vcpu、mem_gb、disk_gb(含 100G 实例盘)、price_hourly numeric(12,4)、max_gpus_per_instance、cuda_max、status(on/off)、created_at
- `images`:平台镜像树 framework→version→python→cuda→image_ref、is_prewarmed
- `admin_users`:username 唯一、password_hash、role(admin/ops/finance/readonly)、status

## API
- 用户:`GET /skus?tier=&gpu_model=`(含 available_count,30s 进程内缓存;仅 on 架)、`GET /images`
- 管理:`POST /api/admin/v1/auth/login`;`CRUD /api/admin/v1/skus`(含上下架);显存超卖>1.2 仅提示不拦截(拦截在前端二次确认)
- 库存来源:WP3 前用 stub(返回配置常量);WP3 后接 orchestrator service 的容量估算

## 验收
- tier/gpu_model 过滤正确;off 架 SKU 用户端不可见
- 30s 缓存生效(窗口内不重复计算)
- admin 角色 readonly 不能写(403);finance 不能改 SKU
- SKU 变更仅影响新实例(实例落库时快照 price_hourly 与规格)
