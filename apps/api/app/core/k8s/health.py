"""从 real/fake 探测行组装组件状态、面板事实和对象明细。"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.core.k8s.base import (
    ComponentDetail,
    ComponentFact,
    ComponentFacts,
    ComponentObject,
    ComponentState,
    FactTone,
)

MAX_OBJECTS = 20


@dataclass(frozen=True)
class WorkloadRow:
    """工作负载的就绪事实;desired=0 可表示未找到对象或期望副本数为零。"""

    name: str
    namespace: str = ""
    ready: int = 0
    desired: int = 0
    image: str = ""
    reason: str = ""


@dataclass(frozen=True)
class NodeRow:
    """一个 Node 的调度事实。"""

    name: str
    pool: str = ""
    ready: bool = False
    schedulable: bool = True
    kubelet: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ListenerRow:
    """Gateway 的一个 listener。attached 是 status 里的 attachedRoutes。"""

    name: str
    port: int = 0
    protocol: str = ""
    attached: int = 0
    programmed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class StorageClassRow:
    name: str
    provisioner: str = ""
    binding_mode: str = ""
    expandable: bool = False
    reclaim: str = ""
    is_default: bool = False


@dataclass(frozen=True)
class RuntimeClassRow:
    name: str
    handler: str = ""
    node_selector: str = ""


def _ratio(ready: int, desired: int) -> str:
    return f"{ready}/{desired}"


def rollout_state(ready: int, desired: int) -> ComponentState:
    """desired 或 ready 非正时为 down;ready 达到 desired 为 ok,否则为 degraded。"""
    if desired <= 0 or ready <= 0:
        return "down"
    return "ok" if ready >= desired else "degraded"


def _worst(*states: ComponentState) -> ComponentState:
    order: list[ComponentState] = ["down", "degraded", "disabled", "unknown", "ok"]
    for s in order:
        if s in states:
            return s
    return "ok"


def _totals(rows: Sequence[WorkloadRow]) -> tuple[int, int]:
    return sum(r.ready for r in rows), sum(r.desired for r in rows)


def workload_objects(rows: Sequence[WorkloadRow]) -> tuple[ComponentObject, ...]:
    """对象表:未就绪的排前面,超出上限截断。"""
    ordered = sorted(rows, key=lambda r: (r.desired > 0 and r.ready >= r.desired, r.name))
    return tuple(
        ComponentObject(
            name=r.name,
            fields={
                "namespace": r.namespace,
                "ready": _ratio(r.ready, r.desired),
                "image": r.image,
                "reason": r.reason,
            },
        )
        for r in ordered[:MAX_OBJECTS]
    )


def _first_reason(rows: Sequence[WorkloadRow]) -> str:
    for r in rows:
        if r.reason and (r.desired <= 0 or r.ready < r.desired):
            return r.reason
    return ""


def nodes_facts(rows: Sequence[NodeRow], pools_ready: dict[str, int]) -> ComponentFacts:
    """判据:每个 Node 的 Ready=True 且未被 cordon。"""
    total = len(rows)
    ready = sum(1 for r in rows if r.ready and r.schedulable)
    cordoned = sum(1 for r in rows if r.schedulable is False)
    not_ready = sum(1 for r in rows if not r.ready)
    state: ComponentState = "down" if ready == 0 else ("ok" if ready == total else "degraded")
    pools = " · ".join(f"{k} {v}" for k, v in sorted(pools_ready.items()) if k != "unlabeled")
    problems = [r for r in rows if not (r.ready and r.schedulable)]
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="ready", value=_ratio(ready, total)),
        facts=(
            ComponentFact(
                key="unschedulable",
                value=str(cordoned),
                tone="warn" if cordoned else "normal",
            ),
            ComponentFact(
                key="notReady", value=str(not_ready), tone="bad" if not_ready else "normal"
            ),
            ComponentFact(key="pools", value=pools or "-"),
        ),
        objects=tuple(
            ComponentObject(
                name=r.name,
                fields={
                    "status": _node_status(r),
                    "pool": r.pool or "-",
                    "kubelet": r.kubelet,
                    "reason": r.reason,
                },
            )
            for r in problems[:MAX_OBJECTS]
        ),
    )


def _node_status(r: NodeRow) -> str:
    if not r.ready:
        return "NotReady"
    return "Ready" if r.schedulable else "Cordoned"


def hami_facts(
    scheduler: WorkloadRow, device_plugin: WorkloadRow, allocatable_gpu: int
) -> ComponentFacts:
    """按 scheduler 副本就绪比判状态;其为 ok 时再采用 device-plugin 的状态。"""
    sched_state = rollout_state(scheduler.ready, scheduler.desired)
    plugin_state = rollout_state(device_plugin.ready, device_plugin.desired)
    state = sched_state if sched_state != "ok" else _worst(plugin_state, "ok")
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="scheduler", value=_ratio(scheduler.ready, scheduler.desired)),
        facts=(
            ComponentFact(
                key="devicePlugin",
                value=_ratio(device_plugin.ready, device_plugin.desired),
                tone="bad" if plugin_state == "down" else _tone(plugin_state),
            ),
            ComponentFact(key="allocatableGpu", value=str(allocatable_gpu)),
            ComponentFact(key="reason", value=_first_reason((scheduler, device_plugin))),
        ),
        objects=workload_objects((scheduler, device_plugin)),
    )


def _tone(state: ComponentState) -> FactTone:
    if state == "down":
        return "bad"
    return "warn" if state == "degraded" else "normal"


def gpu_operator_facts(operands: Sequence[WorkloadRow], driver_version: str) -> ComponentFacts:
    """按 operand 汇总就绪数与期望数判状态;无 operand 为 down。"""
    ready, desired = _totals(operands)
    state = rollout_state(ready, desired) if operands else "down"
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="operand", value=_ratio(ready, desired)),
        facts=(
            ComponentFact(key="driverVersion", value=driver_version or "-"),
            ComponentFact(key="operandCount", value=str(len(operands))),
            ComponentFact(key="reason", value=_first_reason(operands)),
        ),
        objects=workload_objects(operands),
    )


def dcgm_facts(exporters: Sequence[WorkloadRow]) -> ComponentFacts:
    """按 exporter 汇总就绪数与期望数判状态;无 exporter 为 down。"""
    ready, desired = _totals(exporters)
    state = rollout_state(ready, desired) if exporters else "down"
    image = next((r.image for r in exporters if r.image), "")
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="exporter", value=_ratio(ready, desired)),
        facts=(
            ComponentFact(key="image", value=image or "-"),
            ComponentFact(key="reason", value=_first_reason(exporters)),
        ),
        objects=workload_objects(exporters),
    )


def nvidia_runtimeclass_facts(
    runtime_classes: Sequence[RuntimeClassRow], nvidia_nodes: int
) -> ComponentFacts:
    """判据:RuntimeClass nvidia 存在。"""
    row = next((r for r in runtime_classes if r.name == "nvidia"), None)
    return ComponentFacts(
        state="ok" if row else "down",
        headline=ComponentFact(key="present", value="nvidia" if row else "-"),
        facts=(
            ComponentFact(key="handler", value=row.handler if row else "-"),
            ComponentFact(key="applicableNodes", value=str(nvidia_nodes)),
        ),
        objects=_runtime_class_objects(runtime_classes),
    )


def kata_runtimeclass_facts(
    runtime_classes: Sequence[RuntimeClassRow],
    kata_deploy: WorkloadRow,
    kata_nodes: Sequence[NodeRow],
) -> ComponentFacts:
    """缺 kata-qemu 为 down;无就绪且可调度节点为 disabled,否则按 kata-deploy 就绪比判定。"""
    row = next((r for r in runtime_classes if r.name == "kata-qemu"), None)
    ready_nodes = [n for n in kata_nodes if n.ready and n.schedulable]
    if row is None:
        state: ComponentState = "down"
    elif not ready_nodes:
        state = "disabled"
    else:
        state = _worst(rollout_state(kata_deploy.ready, kata_deploy.desired), "ok")
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="poolNodes", value=str(len(ready_nodes))),
        facts=(
            ComponentFact(key="runtimeClass", value=row.name if row else "-"),
            ComponentFact(key="handler", value=row.handler if row else "-"),
            ComponentFact(key="kataDeploy", value=_ratio(kata_deploy.ready, kata_deploy.desired)),
        ),
        objects=tuple(
            ComponentObject(
                name=n.name,
                fields={"status": _node_status(n), "pool": n.pool, "kubelet": n.kubelet},
            )
            for n in kata_nodes[:MAX_OBJECTS]
        ),
    )


def _runtime_class_objects(rows: Sequence[RuntimeClassRow]) -> tuple[ComponentObject, ...]:
    return tuple(
        ComponentObject(name=r.name, fields={"handler": r.handler, "nodeSelector": r.node_selector})
        for r in sorted(rows, key=lambda r: r.name)[:MAX_OBJECTS]
    )


def storage_facts(
    rows: Sequence[StorageClassRow], instance_disk_sc: str, data_disk_sc: str
) -> ComponentFacts:
    """实例盘 SC 存在则为 ok,否则为 down;缺数据盘 SC 只标记 warning 事实。"""
    names = {r.name for r in rows}
    instance_ok = instance_disk_sc in names
    data_ok = data_disk_sc in names
    state: ComponentState = "ok" if instance_ok else "down"
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="count", value=str(len(rows))),
        facts=(
            ComponentFact(
                key="instanceDisk",
                value=instance_disk_sc if instance_ok else "-",
                tone="normal" if instance_ok else "bad",
            ),
            ComponentFact(
                key="dataDisk",
                value=data_disk_sc if data_ok else "-",
                tone="normal" if data_ok else "warn",
            ),
        ),
        objects=tuple(
            ComponentObject(
                name=r.name,
                fields={
                    "provisioner": r.provisioner,
                    "bindingMode": r.binding_mode,
                    "expandable": "true" if r.expandable else "false",
                    "reclaim": r.reclaim,
                    "default": "true" if r.is_default else "false",
                },
            )
            for r in sorted(rows, key=lambda r: r.name)[:MAX_OBJECTS]
        ),
    )


def gateway_facts(
    programmed: bool, address: str, listeners: Sequence[ListenerRow], reason: str
) -> ComponentFacts:
    """Gateway 未 Programmed 为 down;否则任一 listener 未 Programmed 为 degraded,其余为 ok。"""
    ok_listeners = sum(1 for lis in listeners if lis.programmed)
    total = len(listeners)
    if not programmed:
        state: ComponentState = "down"
    elif total and ok_listeners < total:
        state = "degraded"
    else:
        state = "ok"
    attached = sum(lis.attached for lis in listeners)
    return ComponentFacts(
        state=state,
        headline=ComponentFact(key="listener", value=_ratio(ok_listeners, total)),
        facts=(
            ComponentFact(key="address", value=address or "-"),
            ComponentFact(key="attachedRoutes", value=str(attached)),
            ComponentFact(key="reason", value=reason),
        ),
        objects=tuple(
            ComponentObject(
                name=lis.name,
                fields={
                    "port": str(lis.port),
                    "protocol": lis.protocol,
                    "attachedRoutes": str(lis.attached),
                    "programmed": "true" if lis.programmed else "false",
                    "reason": lis.reason,
                },
            )
            for lis in listeners[:MAX_OBJECTS]
        ),
    )


def cert_manager_facts(deploys: Sequence[WorkloadRow]) -> ComponentFacts:
    """按 cert-manager 控制器就绪比判定;其为 ok 但组件汇总未全就绪时为 degraded。"""
    ready, desired = _totals(deploys)
    controller = next((d for d in deploys if d.name == "cert-manager"), WorkloadRow("cert-manager"))
    state = rollout_state(controller.ready, controller.desired)
    if state == "ok" and ready < desired:
        state = "degraded"
    return ComponentFacts(
        state=state,
        headline=ComponentFact(
            key="controller", value=_ratio(controller.ready, controller.desired)
        ),
        facts=(
            ComponentFact(key="components", value=_ratio(ready, desired)),
            ComponentFact(key="reason", value=_first_reason(deploys)),
        ),
        objects=workload_objects(deploys),
    )


def monitoring_facts(prometheus: WorkloadRow, alertmanager: WorkloadRow) -> ComponentFacts:
    """按 Prometheus 就绪比判定;其为 ok 时再采用 Alertmanager 的状态。"""
    prom_state = rollout_state(prometheus.ready, prometheus.desired)
    am_state = rollout_state(alertmanager.ready, alertmanager.desired)
    state = prom_state if prom_state != "ok" else _worst(am_state, "ok")
    return ComponentFacts(
        state=state,
        headline=ComponentFact(
            key="prometheus", value=_ratio(prometheus.ready, prometheus.desired)
        ),
        facts=(
            ComponentFact(
                key="alertmanager",
                value=_ratio(alertmanager.ready, alertmanager.desired),
                tone=_tone(am_state),
            ),
            ComponentFact(key="reason", value=_first_reason((prometheus, alertmanager))),
        ),
        objects=workload_objects((prometheus, alertmanager)),
    )


def merge_facts(base: ComponentFacts, extra: Sequence[ComponentFact]) -> ComponentFacts:
    """追加事实(巡检侧的 Prometheus 补充)。同 key 覆盖,不改 state。"""
    if not extra:
        return base
    by_key = {f.key: f for f in base.facts}
    for f in extra:
        by_key[f.key] = f
    return replace(base, facts=tuple(by_key.values()))


@dataclass(frozen=True)
class ProbeRows:
    """一次探测取到的全部原始行。real 从 K8s 对象取,fake 合成,装配只有 build_facts 一份。"""

    nodes: Sequence[NodeRow] = ()
    hami_scheduler: WorkloadRow = WorkloadRow("hami-scheduler")
    hami_device_plugin: WorkloadRow = WorkloadRow("hami-device-plugin")
    gpu_operands: Sequence[WorkloadRow] = ()
    dcgm: Sequence[WorkloadRow] = ()
    cert_manager: Sequence[WorkloadRow] = ()
    kata_deploy: WorkloadRow = WorkloadRow("kata-deploy")
    prometheus: WorkloadRow = WorkloadRow("prometheus")
    alertmanager: WorkloadRow = WorkloadRow("alertmanager")
    runtime_classes: Sequence[RuntimeClassRow] = ()
    storage_classes: Sequence[StorageClassRow] = ()
    gateway_programmed: bool = False
    gateway_address: str = ""
    listeners: Sequence[ListenerRow] = ()
    gateway_reason: str = "NotFound"
    allocatable_gpu: int = 0
    driver_version: str = ""


def pool_counts(nodes: Sequence[NodeRow]) -> tuple[dict[str, int], dict[str, int]]:
    """(池→节点数,池→Ready 且可调度的节点数)。档位可用性只认后者。"""
    pools: dict[str, int] = {}
    ready: dict[str, int] = {}
    for n in nodes:
        pools[n.pool] = pools.get(n.pool, 0) + 1
        if n.ready and n.schedulable:
            ready[n.pool] = ready.get(n.pool, 0) + 1
    return pools, ready


def build_facts(
    rows: ProbeRows, *, instance_disk_sc: str, data_disk_sc: str
) -> dict[str, ComponentFacts]:
    """十个体检项的事实。键与 nodes 模块的 ComponentKey 一一对应。"""
    _, pools_ready = pool_counts(rows.nodes)
    nodes_ready = sum(pools_ready.values())
    return {
        "nodes": nodes_facts(rows.nodes, pools_ready),
        "hami": hami_facts(rows.hami_scheduler, rows.hami_device_plugin, rows.allocatable_gpu),
        "gpu_operator": gpu_operator_facts(rows.gpu_operands, rows.driver_version),
        "dcgm": dcgm_facts(rows.dcgm),
        "nvidia_runtimeclass": nvidia_runtimeclass_facts(rows.runtime_classes, nodes_ready),
        "kata_runtimeclass": kata_runtimeclass_facts(
            rows.runtime_classes,
            rows.kata_deploy,
            [n for n in rows.nodes if n.pool == "kata"],
        ),
        "storage": storage_facts(rows.storage_classes, instance_disk_sc, data_disk_sc),
        "gateway": gateway_facts(
            rows.gateway_programmed, rows.gateway_address, rows.listeners, rows.gateway_reason
        ),
        "cert_manager": cert_manager_facts(rows.cert_manager),
        "monitoring": monitoring_facts(rows.prometheus, rows.alertmanager),
    }


def merge_details(a: ComponentDetail, b: ComponentDetail) -> ComponentDetail:
    """合并两次深探结果(如 cert-manager 的 Pod 现场 + 证书列表)。"""
    return ComponentDetail(
        facts=a.facts + b.facts, pods=a.pods + b.pods, events=a.events + b.events
    )
