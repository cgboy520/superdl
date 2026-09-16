"""Assemble component states, panel facts and object details from real/fake probe rows."""

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
    """Readiness facts of a workload; desired=0 may mean no object found or zero desired
    replicas."""

    name: str
    namespace: str = ""
    ready: int = 0
    desired: int = 0
    image: str = ""
    reason: str = ""


@dataclass(frozen=True)
class NodeRow:
    """Scheduling facts of one Node."""

    name: str
    pool: str = ""
    ready: bool = False
    schedulable: bool = True
    kubelet: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ListenerRow:
    """One Gateway listener. attached is attachedRoutes from status."""

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
    """down when desired or ready is non-positive; ok when ready reaches desired, otherwise
    degraded."""
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
    """Object table: unready first, truncated at the cap."""
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
    """Criterion: every Node has Ready=True and is not cordoned."""
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
    """Judged by the scheduler replica readiness ratio; when ok, the device-plugin state is
    adopted."""
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
    """Judged by ready vs desired summed over the operands; no operand = down."""
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
    """Judged by ready vs desired summed over the exporters; no exporter = down."""
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
    """Criterion: RuntimeClass nvidia exists."""
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
    """Missing kata-qemu = down; no ready schedulable node = disabled, otherwise judged by the
    kata-deploy readiness ratio."""
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
    """ok when the instance-disk SC exists, otherwise down; a missing data-disk SC only marks a
    warning fact."""
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
    """Gateway not Programmed = down; otherwise any listener not Programmed = degraded, else ok."""
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
    """Judged by the cert-manager controller readiness ratio; ok there but components not all ready
    = degraded."""
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
    """Judged by the Prometheus readiness ratio; when ok, the Alertmanager state is adopted."""
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
    """Append facts (Prometheus supplements from the patrol). Same key overrides, state
    unchanged."""
    if not extra:
        return base
    by_key = {f.key: f for f in base.facts}
    for f in extra:
        by_key[f.key] = f
    return replace(base, facts=tuple(by_key.values()))


@dataclass(frozen=True)
class ProbeRows:
    """All raw rows of one probe. real takes them from K8s objects, fake synthesises them; assembly
    happens only in build_facts."""

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
    """(pool → node count, pool → Ready and schedulable node count). Tier availability trusts the
    latter only."""
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
    """Facts of the ten health-check items. Keys map one to one to the nodes module's
    ComponentKey."""
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
    """Merge two deep-probe results (e.g. cert-manager's Pod scene + certificate list)."""
    return ComponentDetail(
        facts=a.facts + b.facts, pods=a.pods + b.pods, events=a.events + b.events
    )
