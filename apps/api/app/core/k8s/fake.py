"""内存态 FakeOrchestrator:dev/test 默认后端。

行为可注入:auto_ready(Pod 立即 Ready)、graceful_delete(优雅删除期)、
kill_pod / mark_unready / inject_leaked_pod(reconciler 场景)、
fail_next_quota / fail_next_logs / fail_probe(单次失败注入)。容量按 pool 配置。
"""

from dataclasses import dataclass, field

from app.core.k8s.base import (
    GPU_MODEL_NODE_LABEL,
    INSTANCE_DISK_STORAGE_CLASS,
    JOB_NAME_LABEL,
    JUICEFS_STORAGE_CLASS,
    MANAGED_LABEL,
    POOL_NODE_LABEL,
    ClusterProbe,
    InstancePodSpec,
    NodePortTaken,
    PodStatus,
    PrewarmJobStatus,
    derive_distro,
)

_FAKE_K8S_VERSION = "v1.36.2+rke2r1"  # 探测默认健康 RKE2


@dataclass
class _FakePod:
    spec: InstancePodSpec
    ready: bool
    phase: str = "Running"
    node_name: str = "fake-node-1"
    deleting: bool = False  # Terminating:deletionTimestamp 已设,对象仍在
    labels: dict[str, str] = field(default_factory=lambda: {MANAGED_LABEL: "true"})


@dataclass
class FakeOrchestrator:
    auto_ready: bool = True
    # 模拟真实 K8s 的优雅删除:对象在 etcd 里再留 terminationGracePeriodSeconds,期间
    # read 仍 200、phase 仍 Running。默认关,复现「删了又立刻同名重建」的时序问题时打开。
    graceful_delete: bool = False
    # 池 → 该池合成节点的 GPU 数。cpu 池恒 0 卡:不给它一个节点,纯 CPU 档在 dev 与
    # e2e 里就是零库存、点不进去,整条 CPU 路径无从验证
    pool_capacity: dict[str, int] = field(
        default_factory=lambda: {"kata": 16, "hami": 32, "mig": 16, "cpu": 0}
    )
    pods: dict[tuple[str, str], _FakePod] = field(default_factory=dict)
    namespaces: set[str] = field(default_factory=set)
    # 实例盘 PVC:(ns, name) -> 盘标记。独立于 Pod 生命周期,只有释放/回收才删;
    # 标记值用于断言「还是原来那块盘」。
    instance_disks: dict[tuple[str, str], str] = field(default_factory=dict)
    wiped_disks: list[tuple[str, str]] = field(default_factory=list)
    # 数据盘目录配额:subpath -> capacity_gb;fail_next_quota 注入一次下发失败
    disk_quotas: dict[str, int] = field(default_factory=dict)
    fail_next_quota: bool = False
    # 擦除异步语义:auto_wipe=False 时 wipe_disk 进入「进行中」(抛错,对齐真实 Job),
    # finish_wipe 标记完成后调用返回
    auto_wipe: bool = True
    wipe_completed: set[tuple[str, str]] = field(default_factory=set)
    # 受管 Job(wipe)运行中的 Pod:(ns, pod_name) -> labels。独立于 self.pods:
    # Job Pod 不是实例(无 InstancePodSpec/NodePort),但 real 里它带 MANAGED_LABEL
    # 会被全量 LIST 命中——不登记则测试复现不了「泄漏回收误杀擦盘 Job」的场景
    job_pods: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    # 预热:(node_name, image_ref) -> state;auto_prewarm=True 时创建即 succeeded
    prewarm_jobs: dict[tuple[str, str], str] = field(default_factory=dict)
    # 预热 Job 引用的拉取凭据 Secret 名(None = 未配机器人),测试据此断言凭据链路
    prewarm_pull_secrets: dict[tuple[str, str], str | None] = field(default_factory=dict)
    # 平台托管的拉取凭据 Secret:ns -> 指纹(对齐 real 的 annotation 语义)
    pull_secrets: dict[str, str] = field(default_factory=dict)
    auto_prewarm: bool = True
    # 注入节点:追加在合成节点之后
    extra_nodes: list = field(default_factory=list)
    unlabeled_nodes: list = field(default_factory=list)  # include_unlabeled 时附加
    node_labels: dict[str, dict[str, str]] = field(default_factory=dict)  # set_node_labels 落点
    # cordon 状态:节点名集合,list_nodes 反映为 Cordoned
    cordoned_nodes: set[str] = field(default_factory=set)
    # Service/Ingress 端点(create 注册/delete 移除);测试可手工注入孤儿端点
    endpoints: set[tuple[str, str]] = field(default_factory=set)
    # 外部占用的 NodePort(非平台对象的 Service,如无 MANAGED_LABEL 的第三方服务):
    # 撞占时 create_instance 抛 NodePortTaken(对齐 real 的 422 归一化),
    # used_node_ports 必须看得见它——否则 blocked 端口周期复检会把真占用误判为已释放
    external_node_ports: set[int] = field(default_factory=set)
    # per-instance 敏感 env 的「Secret」(对齐 real 的 instance_env_secret_name 生命周期):
    # 测试据此断言 token 不落 Pod spec,而是走 secretKeyRef
    instance_secrets: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    # 能力探测:默认健康 RKE2;fail_probe 模拟断连
    probe_hami_ready: bool = True
    probe_kata_runtimeclass: bool = True
    probe_k8s_version: str = _FAKE_K8S_VERSION  # 改成 +k3s1 即模拟 light 档
    fail_probe: bool = False
    # 容器日志:fail_next_logs 注入一次读取失败;log_calls 记录调用参数供断言
    fail_next_logs: bool = False
    log_calls: list[tuple[str, str, int, int | None]] = field(default_factory=list)

    async def ensure_namespace(self, namespace: str) -> None:
        self.namespaces.add(namespace)

    async def ensure_pull_secret(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        self.pull_secrets[namespace] = fingerprint

    async def probe_cluster(self) -> ClusterProbe:
        if self.fail_probe:
            return ClusterProbe(api_reachable=False, error="fake: connection refused")
        pools: dict[str, int] = {}
        for n in await self.list_nodes(include_unlabeled=True):
            key = n.pool_label if n.pool_label not in ("", "unknown") else "unlabeled"
            pools[key] = pools.get(key, 0) + 1
        return ClusterProbe(
            api_reachable=True,
            k8s_version=self.probe_k8s_version,
            distro=derive_distro(self.probe_k8s_version),
            hami_ready=self.probe_hami_ready,
            dcgm_present=True,
            kps_present=True,
            gpu_operator_present=True,
            kata_runtimeclass=self.probe_kata_runtimeclass,
            nvidia_runtimeclass=True,
            gateway_ready=True,
            cert_manager_ready=True,
            nodes_ready=sum(pools.values()),
            nodes_total=sum(pools.values()),
            storage_classes=(JUICEFS_STORAGE_CLASS, INSTANCE_DISK_STORAGE_CLASS),
            pools=pools,
        )

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        key = (namespace, subpath)
        if key in self.wipe_completed:
            # 真实语义:Job 已成功 → 清理并返回(擦除只记录这一次)
            self.wipe_completed.discard(key)
            self.wiped_disks.append(key)
            return
        if self.auto_wipe:
            self.wiped_disks.append(key)
            return
        # 进行中:抛错交 outbox 退避重试(对齐 real._run_managed_job_sync);
        # 同时登记 wipe Job 的 Pod(real 里 Job 创建后 Pod 即存在直至成功清理)
        self.job_pods[(namespace, f"wipe-{subpath}")] = {
            MANAGED_LABEL: "true",
            JOB_NAME_LABEL: f"wipe-{subpath}",
        }
        raise RuntimeError(f"fake: wipe in progress: {subpath}")

    def finish_wipe(self, namespace: str, subpath: str) -> None:
        """测试注入:擦除作业完成;下次 wipe_disk 调用清理并返回成功。"""
        self.wipe_completed.add((namespace, subpath))
        self.job_pods.pop((namespace, f"wipe-{subpath}"), None)

    async def set_disk_quota(self, subpath: str, capacity_gb: int) -> None:
        if self.fail_next_quota:
            self.fail_next_quota = False
            raise RuntimeError("fake: set_disk_quota failed (injected)")
        self.disk_quotas[subpath] = capacity_gb

    async def delete_disk_quota(self, subpath: str) -> None:
        self.disk_quotas.pop(subpath, None)

    async def create_instance(self, spec: InstancePodSpec) -> None:
        if spec.with_ssh and spec.ssh_node_port is None:
            # 对齐 real:_create_service_sync 在这种组合下抛 RuntimeError
            raise RuntimeError(f"fake: instance {spec.name} wants ssh but has no node port")
        if spec.service_port is not None and not spec.service_host:
            # 对齐 real:_httproute_body 缺 hostname 时抛 RuntimeError
            raise RuntimeError(f"fake: instance {spec.name} has service_port but no service_host")
        if spec.ssh_node_port is not None and spec.ssh_node_port in self.external_node_ports:
            # 对齐 real:apiserver 422 "provided port is already allocated" 的归一化
            raise NodePortTaken(spec.ssh_node_port)
        key = (spec.namespace, spec.name)
        if spec.secret_env:
            self.instance_secrets[key] = dict(spec.secret_env)
        # 实例盘已存在即复用(重新开机不重建盘);首次创建才落一个新 token
        self.instance_disks.setdefault(key, f"lv-{spec.name}")
        existing = self.pods.get(key)
        if existing is not None:
            if existing.deleting:
                # 真实集群里同名对象 Terminating 时 create 返回 409,不可当幂等跳过
                raise RuntimeError(f"fake: pod {spec.name} is terminating, create must wait")
            return  # 幂等
        self.pods[key] = _FakePod(
            spec=spec, ready=self.auto_ready, phase="Running" if self.auto_ready else "Pending"
        )
        self.endpoints.add(key)

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        if self.graceful_delete and not force:
            pod = self.pods.get((namespace, name))
            if pod is not None:
                pod.deleting = True  # Terminating:对象仍在,exists 仍为 True
                pod.ready = False
            return
        self.pods.pop((namespace, name), None)  # 注意:不碰 instance_disks
        self.instance_secrets.pop((namespace, name), None)
        self.endpoints.discard((namespace, name))

    def finish_delete(self, namespace: str, name: str) -> None:
        """测试注入:优雅期结束,对象真正从 etcd 消失。"""
        self.pods.pop((namespace, name), None)

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        return sorted(self.endpoints)

    async def used_node_ports(self) -> set[int]:
        # 与 real 同口径:平台 Pod 占用 + 外部对象占用(不带平台标签的 Service 也算)
        return {
            p.spec.ssh_node_port for p in self.pods.values() if p.spec.ssh_node_port is not None
        } | set(self.external_node_ports)

    def inject_external_port(self, port: int) -> None:
        """测试注入:集群里出现一个非平台对象占用了该 NodePort。"""
        self.external_node_ports.add(port)

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        self.instance_disks.pop((namespace, name), None)

    async def get_status(self, namespace: str, name: str) -> PodStatus:
        pod = self.pods.get((namespace, name))
        if pod is None:
            return PodStatus(exists=False)
        return PodStatus(
            exists=True,
            ready=pod.ready,
            phase=pod.phase,
            node_name=pod.node_name,
            deleting=pod.deleting,
            namespace=namespace,
            name=name,
        )

    async def read_instance_logs(
        self, namespace: str, name: str, *, tail_lines: int, since_seconds: int | None = None
    ) -> str:
        """合成日志:带时间戳的固定几行(含实例名),不按 Pod 存在性报错——
        dev 下 API 与 worker 是两个进程,内存态 Pod 不同步,存在性报错会让前端联调恒失败。
        失败路径由 fail_next_logs 注入覆盖。"""
        if self.fail_next_logs:
            self.fail_next_logs = False
            raise RuntimeError("fake: read_instance_logs failed (injected)")
        self.log_calls.append((namespace, name, tail_lines, since_seconds))
        lines = [
            f"2026-08-23T03:14:01Z [entrypoint] instance {name} booting",
            "2026-08-23T03:14:01Z [entrypoint] mounting instance disk at /root",
            "2026-08-23T03:14:02Z [entrypoint] starting sshd on :22",
            "2026-08-23T03:14:02Z [sshd] Server listening on 0.0.0.0 port 22",
            "2026-08-23T03:14:03Z [entrypoint] starting jupyter…",
            "2026-08-23T03:14:03Z [jupyter] Jupyter Server 2.16.0 is running at http://0.0.0.0:8888/lab",
            f"2026-08-23T03:14:04Z [jupyter] incoming websocket from console ({name})",
            "2026-08-23T03:14:05Z [entrypoint] bootstrap done, workspace ready",
        ]
        return "\n".join(lines[-tail_lines:])

    async def list_instance_pods(self) -> list[PodStatus]:
        entries = [
            PodStatus(
                exists=True,
                ready=pod.ready,
                phase=pod.phase,
                node_name=pod.node_name,
                deleting=pod.deleting,
                namespace=ns,
                name=name,
                labels=dict(pod.labels),
            )
            for (ns, name), pod in self.pods.items()
        ]
        entries.extend(
            PodStatus(
                exists=True,
                phase="Running",
                node_name="fake-node-1",
                namespace=ns,
                name=name,
                labels=dict(labels),
            )
            for (ns, name), labels in self.job_pods.items()
        )
        return entries

    # ---------- 预热 ----------

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        self.prewarm_pull_secrets[(node_name, image_ref)] = image_pull_secret
        # setdefault = 幂等:已有 Job(任意状态)不重建
        self.prewarm_jobs.setdefault(
            (node_name, image_ref), "succeeded" if self.auto_prewarm else "running"
        )

    async def get_prewarm_status(self, node_name: str, image_ref: str) -> PrewarmJobStatus:
        state = self.prewarm_jobs.get((node_name, image_ref))
        if state is None:
            return PrewarmJobStatus(state="absent")
        message = "fake: ErrImagePull" if state == "failed" else None
        return PrewarmJobStatus(state=state, message=message)

    async def delete_prewarm_job(self, node_name: str, image_ref: str) -> None:
        self.prewarm_jobs.pop((node_name, image_ref), None)

    def set_prewarm_state(self, node_name: str, image_ref: str, state: str) -> None:
        """测试注入:直接改 Job 状态(running/succeeded/failed)。"""
        self.prewarm_jobs[(node_name, image_ref)] = state

    # ---------- 测试注入 ----------

    def kill_pod(self, namespace: str, name: str) -> None:
        """模拟 Pod 意外消失(节点故障)。"""
        self.pods.pop((namespace, name), None)

    def mark_unready(self, namespace: str, name: str) -> None:
        """模拟节点失联:kubelet 不可达,Ready 转 False 而 phase 仍是 Running、对象仍在。"""
        pod = self.pods[(namespace, name)]
        pod.ready = False
        pod.phase = "Running"

    def mark_ready(self, namespace: str, name: str) -> None:
        pod = self.pods[(namespace, name)]
        pod.ready = True
        pod.phase = "Running"

    def inject_leaked_pod(self, namespace: str, name: str, spec: InstancePodSpec) -> None:
        """模拟 DB 已 released 但 K8s 残留的泄漏 Pod。"""
        self.pods[(namespace, name)] = _FakePod(spec=spec, ready=True)

    async def list_nodes(self, include_unlabeled: bool = False):
        """节点视图(Fake:按池合成节点;include_unlabeled 时附无标签节点)。"""
        from app.core.k8s.base import NodeInfo

        models = {"kata": "RTX4090", "hami": "RTX4090", "mig": "H100"}
        nodes = []
        for pool, cap in self.pool_capacity.items():
            used = min(
                cap,
                sum(
                    int(p.spec.gpu_resources.get("nvidia.com/gpu", "0"))
                    for p in self.pods.values()
                    if p.spec.node_selector.get(POOL_NODE_LABEL) == pool
                ),
            )
            name = f"fake-{pool}-node-1"
            nodes.append(
                NodeInfo(
                    name=name,
                    pool_label=pool,
                    gpu_total=cap,
                    gpu_used=used,
                    status="Ready",
                    vcpu=64,
                    mem_gb=512,
                    disk_gb=2048,
                    gpu_model_label=models.get(pool, ""),
                    model_label_current=self.node_labels.get(name, {}).get(
                        GPU_MODEL_NODE_LABEL, ""
                    ),
                )
            )
        if include_unlabeled:
            nodes.extend(self.unlabeled_nodes)
        nodes.extend(self.extra_nodes)
        return [
            NodeInfo(
                name=n.name,
                pool_label=n.pool_label,
                gpu_total=n.gpu_total,
                gpu_used=n.gpu_used,
                status="Cordoned" if n.name in self.cordoned_nodes else n.status,
                vcpu=n.vcpu,
                mem_gb=n.mem_gb,
                disk_gb=n.disk_gb,
                gpu_model_label=n.gpu_model_label,
                model_label_current=n.model_label_current
                or self.node_labels.get(n.name, {}).get(GPU_MODEL_NODE_LABEL, ""),
                driver_version_label=n.driver_version_label,
                cuda_version_label=n.cuda_version_label,
            )
            for n in nodes
        ]

    def inject_node(self, node) -> None:
        """模拟新 GPU 节点加入集群。传 NodeInfo。"""
        self.extra_nodes.append(node)

    async def set_node_labels(self, node_name: str, labels: dict[str, str]) -> None:
        self.node_labels.setdefault(node_name, {}).update(labels)

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        if unschedulable:
            self.cordoned_nodes.add(node_name)
        else:
            self.cordoned_nodes.discard(node_name)
