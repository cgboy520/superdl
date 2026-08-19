# WP3 · 编排核心

## 目标
实例状态机 + outbox 编排 + reconciler 对账 + 端口池。K8s 走接口抽象,单测全 mock,
kind 集成测试断言对象结构与 reconciler 行为。

## 数据
- `instances`:uuid、user_id、sku 快照(sku_id + spec_snapshot jsonb + price_hourly)、gpu_count、status、k8s(namespace/pod_name/node_name)、ssh_port?、jupyter_token、image_ref、data_disk_id?、idempotency_key 唯一?、version(乐观锁)、created_at/updated_at
- `instance_events`:instance_id、from_status、to_status、reason、actor(user/system/admin)、metadata、created_at —— 追加式,计费主依据
- `port_allocations`:port 唯一、instance_id nullable

## 状态机(迁移必须走 service.transition,同事务写事件)
creating→running/failed;running→stopping;stopping→stopped;stopped→starting/frozen/releasing;
starting→running/failed;frozen→stopped/releasing;releasing→released。
running↔非 running 的边 = 计费边。

## K8s 抽象(core/k8s + gpu_adapter)
- `InstanceOrchestrator` 协议:ensure_tenant_namespace / create_instance_pod / delete_instance_pod / get_pod_phase / list_tenant_pods
- FakeOrchestrator(dev/test,内存态,可注入故障)与 RealOrchestrator(kubernetes 36.x)
- gpu_adapter:tier→资源请求语法(HAMi: nvidia.com/gpu+gpucores/gpumem;MIG profile;整卡),RuntimeClass 按池(kata-qemu / runc)

## 流程
- 创建:校验余额≥1h(billing service)→ 事务{instances(creating)+event+outbox(create_instance)} → 202
- worker:ensure ns/quota/netpol → create pod+svc+端口分配 → (reconciler 观察 Ready→running)
- reconciler(30s,advisory lock):Pod Ready 而 DB creating/starting → running(计费开始);Pod 消失而 DB running → failed(停费+告警);Pod 存在而 DB released → 强删;creating 超 5min → failed+退款
- stop/start/release 同构;release 后擦盘任务(blkdiscard 由节点 job,MVP 记事件)

## 验收(AI+kind / FakeOrchestrator)
- kill pod 后 30s 内 DB 转 failed 并停止计费
- 泄漏 pod(DB released)被回收
- creating 超时自动失败退款;端口不泄漏(释放回池)
- 非法迁移(如 running→starting)报 INSTANCE_INVALID_TRANSITION
