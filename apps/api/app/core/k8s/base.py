"""K8s orchestration abstraction: the orchestrator programs against this protocol only, dev/test
use Fake, production uses Real; business code must never
import the kubernetes client directly."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

INSTANCE_DISK_STORAGE_CLASS = "topolvm-provisioner"
DATA_DISK_STORAGE_CLASS = "superdl-cephfs"

WORKSPACE_CONTAINER = "workspace"
STARTUP_PROBE_PERIOD_SECONDS = 10

GATEWAY_NAMESPACE = "superdl"
GATEWAY_NAME = "superdl"
GATEWAY_APP_LISTENER = "app-https"
GATEWAY_SVC_LISTENER = "svc-https"
GATEWAY_API_GROUP = "gateway.networking.k8s.io"
GATEWAY_API_VERSION = "v1"
HTTPROUTE_PLURAL = "httproutes"
GATEWAY_PLURAL = "gateways"

# Tenant egress: never these destinations, never these TCP ports (shared by the tenant and the
# gateway NetworkPolicies and by the gateway peer checks).
PRIVATE_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "198.18.0.0/15",
]
EGRESS_BLOCKED_TCP_PORTS = (
    23,
    25,
    135,
    139,
    445,
    465,
    587,
    3306,
    3389,
    5432,
    6379,
    9200,
    11211,
    27017,
)

NETGW_NAMESPACE = "superdl-netgw"
NETGW_LABEL = "superdl.io/netgw"
NETGW_TENANT_LABEL = "superdl.io/tenant"
# Kubelets cannot set node-restriction.kubernetes.io/ labels: a tenant node cannot attract gateways.
NETGW_NODE_LABEL = "node-restriction.kubernetes.io/superdl-netgw"
NETGW_CONTAINER = "gateway"
NETGW_SOCKS_PORT = 1080
NETGW_HEALTH_PORT = 9101
# L3: attached instance Pods carry this label (value = gateway uuid) and the attach init container.
NETGW_L3_LABEL = "superdl.io/netgw-l3"
NETGW_ATTACH_CONTAINER = "netgw-attach"
NETGW_HUB_PORT = 51820
CLUSTER_DOMAIN = "cluster.local"


def jupyter_service_name(instance_name: str) -> str:
    """Name of Jupyter's ClusterIP Service (separate from the SSH NodePort Service)."""
    return f"{instance_name}-jupyter"


def service_endpoint_service_name(instance_name: str) -> str:
    """Name of the service endpoint's ClusterIP Service (separate from the SSH / Jupyter
    Services)."""
    return f"{instance_name}-svc"


def instance_disk_pvc_name(instance_name: str) -> str:
    """Name of the platform-managed instance disk PVC."""
    return f"{instance_name}-root"


def data_disk_pvc_name(disk_uuid: str) -> str:
    """Name of the data-disk PVC; the caller must pass a valid disk_uuid."""
    return f"disk-{disk_uuid}"


def netgw_object_name(gateway_uuid: str) -> str:
    """Deployment / Secret / NetworkPolicy / SOCKS Service of one gateway (gateway namespace)."""
    return f"netgw-{gateway_uuid}"


def netgw_forward_service_name(gateway_uuid: str, forward_id: int) -> str:
    """Per-forward ClusterIP Service: intranet forwards in the gateway namespace, instance forwards
    in the tenant namespace."""
    return f"netgw-{gateway_uuid}-{forward_id}"


def netgw_instance_forward_host(gateway_uuid: str, forward_id: int, tenant_namespace: str) -> str:
    """Absolute cluster name the gateway's reverse relay dials for one instance forward."""
    name = netgw_forward_service_name(gateway_uuid, forward_id)
    return f"{name}.{tenant_namespace}.svc.{CLUSTER_DOMAIN}."


def netgw_hub_service_name(gateway_uuid: str) -> str:
    """Headless Service of the gateway's L3 hub (gateway namespace); instances dial it by name."""
    return f"netgw-{gateway_uuid}-hub"


def netgw_l3_service_name(gateway_uuid: str) -> str:
    """Headless Service selecting the attached instance Pods (tenant namespace); also their
    spec.subdomain, so each Pod resolves as <instance>.<this>.<namespace>.svc."""
    return f"netgw-{gateway_uuid}-l3"


def netgw_attach_secret_name(instance_name: str) -> str:
    """Per-instance Secret with attach.json, mounted only into the attach init container."""
    return f"netgw-attach-{instance_name}"


def netgw_hub_host(gateway_uuid: str) -> str:
    return f"{netgw_hub_service_name(gateway_uuid)}.{NETGW_NAMESPACE}.svc.{CLUSTER_DOMAIN}."


def netgw_peer_host(instance_name: str, gateway_uuid: str, tenant_namespace: str) -> str:
    """DNS name of one attached instance Pod, re-resolved by the hub every tick."""
    service = netgw_l3_service_name(gateway_uuid)
    return f"{instance_name}.{service}.{tenant_namespace}.svc.{CLUSTER_DOMAIN}."


def instance_env_secret_name(instance_name: str) -> str:
    """Name of the per-instance Secret for sensitive env (JUPYTER_TOKEN etc.), deleted with the
    instance."""
    return f"jupyter-{instance_name}"


@dataclass(frozen=True)
class NetgwAttach:
    """Instance side of an L3 attachment: init container image and attach.json (carries the
    instance's hub private key, so it only lands in a Secret)."""

    gateway_uuid: str
    image: str
    config_json: str


@dataclass(frozen=True)
class InstancePodSpec:
    """K8s parameters of a tenant instance; sensitive environment variables go into secret_env,
    never into env."""

    namespace: str
    name: str
    image: str
    gpu_resources: dict[str, str]
    runtime_class: str | None
    host_users: bool
    vcpu: int
    mem_gb: int
    disk_gb: int
    ssh_node_port: int | None
    jupyter_host: str
    env: dict[str, str] = field(default_factory=dict)
    secret_env: dict[str, str] = field(default_factory=dict)
    authorized_keys: tuple[str, ...] = ()
    node_selector: dict[str, str] = field(default_factory=dict)
    data_disk_pvc: str | None = None
    scheduler_name: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)
    image_pull_secret: str | None = None

    restart_policy: str = "Never"
    command: tuple[str, ...] | None = None
    args: tuple[str, ...] | None = None
    service_port: int | None = None
    service_host: str | None = None
    health_path: str | None = None
    with_ssh: bool = True
    startup_failure_threshold: int = 90
    netgw: NetgwAttach | None = None


@dataclass(frozen=True)
class NetgwIntranetForward:
    """Instances reach `<name>:<port>` (ExternalName in the tenant namespace → per-forward Service
    in the gateway namespace → gateway listen_port)."""

    forward_id: int
    name: str
    port: int
    listen_port: int


@dataclass(frozen=True)
class NetgwInstanceForward:
    """Tenant-namespace ClusterIP Service selecting one instance Pod on `port`; the gateway relays
    the tunnel side to it."""

    forward_id: int
    instance_name: str
    port: int


@dataclass(frozen=True)
class NetgwSpec:
    """K8s parameters of one gateway; spec_json carries secrets and only lands in a Secret.
    peers_json (L3 peers) sits next to it but outside spec_hash: attaching never rolls the Pod.
    tenant_namespace is the owner's namespace of a personal gateway, None for an organization
    gateway, whose tenant-side objects live in l3_namespaces (the attached instances' namespaces;
    outside spec_hash and the Pod labels, so membership changes never roll the Pod)."""

    uuid: str
    tenant_namespace: str | None
    socks_name: str
    image: str
    spec_json: str
    egress_ports: tuple[tuple[str, int], ...]
    intranet: tuple[NetgwIntranetForward, ...] = ()
    instance: tuple[NetgwInstanceForward, ...] = ()
    bandwidth_mbps: int = 0
    image_pull_secret: str | None = None
    l3: bool = False
    peers_json: str = '{"peers":[]}'
    l3_namespaces: tuple[str, ...] = ()

    @property
    def org(self) -> bool:
        return self.tenant_namespace is None

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.spec_json.encode()).hexdigest()


@dataclass(frozen=True)
class NetgwStatus:
    """One gateway Deployment as observed: image of its template, readiness of its Pod."""

    uuid: str
    image: str
    ready: bool
    phase: str


class NodePortTaken(Exception):
    """The requested NodePort is held by another object (apiserver 422); the caller marks it blocked
    and picks another port."""

    def __init__(self, port: int) -> None:
        super().__init__(f"node port {port} already allocated")
        self.port = port


@dataclass(frozen=True)
class PodStatus:
    """Pod status; deleting means deletion was requested but the object still exists, independent of
    phase.
    started_at = when the workspace container first entered running (aware-UTC; None if never)."""

    exists: bool
    ready: bool = False
    phase: str = "Unknown"
    node_name: str | None = None
    deleting: bool = False
    namespace: str = ""
    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None


ComponentState = Literal["ok", "degraded", "down", "disabled", "unknown"]
FactTone = Literal["normal", "warn", "bad"]


@dataclass(frozen=True)
class ComponentFact:
    """One checkable fact. key is the copy suffix (the frontend renders the label), value is pure
    data: counts, versions, object names,
    addresses, durations. value carries no language and does not vary by locale."""

    key: str
    value: str
    tone: FactTone = "normal"


@dataclass(frozen=True)
class ComponentObject:
    """One row of the drawer object table. name is the object name; the keys of fields are column
    suffixes, values are pure data as well."""

    name: str
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ComponentFacts:
    """Probe result of one health-check item. headline is the panel's main number, facts are the
    fact rows of panel and drawer,
    objects the object-level details in the drawer (DaemonSet / listener / StorageClass / node)."""

    state: ComponentState
    headline: ComponentFact | None = None
    facts: tuple[ComponentFact, ...] = ()
    objects: tuple[ComponentObject, ...] = ()


def _fact_to_json(f: ComponentFact) -> dict[str, str]:
    return {"key": f.key, "value": f.value, "tone": f.tone}


def component_facts_to_json(facts: dict[str, ComponentFacts]) -> dict[str, Any]:
    """Convert component facts into JSONB-writable dicts and lists."""
    return {
        key: {
            "state": cf.state,
            "headline": _fact_to_json(cf.headline) if cf.headline else None,
            "facts": [_fact_to_json(f) for f in cf.facts],
            "objects": [{"name": o.name, "fields": dict(o.fields)} for o in cf.objects],
        }
        for key, cf in facts.items()
    }


def component_facts_from_json(raw: Any) -> dict[str, ComponentFacts]:
    """Parse component facts; skip non-dict items or items whose state is not a string, drop invalid
    details and normalise fields."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, ComponentFacts] = {}
    for key, item in raw.items():
        if not isinstance(item, dict) or not isinstance(item.get("state"), str):
            continue
        head = item.get("headline")
        out[str(key)] = ComponentFacts(
            state=item["state"],
            headline=_fact_from_json(head),
            facts=tuple(
                f
                for f in (_fact_from_json(x) for x in _as_list(item.get("facts")))
                if f is not None
            ),
            objects=tuple(
                ComponentObject(name=str(o.get("name", "")), fields=_as_str_map(o.get("fields")))
                for o in _as_list(item.get("objects"))
                if isinstance(o, dict)
            ),
        )
    return out


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _as_str_map(v: Any) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    return {str(k): str(val) for k, val in v.items()}


def _fact_from_json(v: Any) -> ComponentFact | None:
    if not isinstance(v, dict) or not isinstance(v.get("key"), str):
        return None
    tone = v.get("tone")
    return ComponentFact(
        key=v["key"],
        value=str(v.get("value", "")),
        tone=tone if tone in ("normal", "warn", "bad") else "normal",
    )


@dataclass(frozen=True)
class ComponentDetail:
    """Live details of a health-check item: facts, Pod or certificate objects, warning events."""

    facts: tuple[ComponentFact, ...] = ()
    pods: tuple[ComponentObject, ...] = ()
    events: tuple[ComponentObject, ...] = ()


@dataclass(frozen=True)
class ClusterProbe:
    """Cluster capability probe snapshot (the nodes patrol writes it to cluster_status; the gate and
    the cluster page read the table, never probe live)."""

    api_reachable: bool
    k8s_version: str | None = None
    distro: str | None = None
    hami_ready: bool = False
    dcgm_present: bool = False
    kps_present: bool = False
    gpu_operator_present: bool = False
    kata_runtimeclass: bool = False
    nvidia_runtimeclass: bool = False
    gateway_ready: bool = False
    cert_manager_ready: bool = False
    nodes_ready: int = 0
    nodes_total: int = 0
    storage_classes: tuple[str, ...] = ()
    pools: dict[str, int] = field(default_factory=dict)
    pools_ready: dict[str, int] = field(default_factory=dict)
    component_facts: dict[str, ComponentFacts] = field(default_factory=dict)
    error: str | None = None


def derive_distro(git_version: str | None) -> str | None:
    """Distribution derived from the gitVersion suffix; None when unrecognised."""
    if not git_version:
        return None
    if "+rke2" in git_version:
        return "rke2"
    if "+k3s" in git_version:
        return "k3s"
    return None


@dataclass(frozen=True)
class PrewarmJobStatus:
    """Image prewarm Job status."""

    state: str
    message: str | None = None


class K8sOrchestrator(Protocol):
    """Every operation must be idempotent (outbox at-least-once semantics)."""

    async def ensure_namespace(self, namespace: str) -> None:
        """Create or update the tenant namespace labels, RBAC, NetworkPolicy, ResourceQuota and
        LimitRange."""
        ...

    async def ensure_pull_secret(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        """Write / overwrite the image pull Secret in the namespace (dockerconfigjson,
        core/registry.PULL_SECRET_NAME);
        skipped when the annotation fingerprint matches. Idempotent."""
        ...

    async def create_instance(self, spec: InstancePodSpec) -> None:
        """Create the instance disk, env Secret, Pod, Services and HTTPRoute, replay-safe.

        Secret and SSH port converge; a Pod / SSH Service that is being deleted must vanish before
        the retry.
        """
        ...

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        """Delete the instance Pod, Services, HTTPRoute and env Secret; missing objects are ignored,
        the instance disk is kept.
        force=True deletes with zero grace period, only used when the node is lost.
        """
        ...

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        """Delete the instance disk PVC. Only on instance termination (release / reclamation /
        creating timeout); never on stop, restart or pod_lost;
        the call site must confirm the Pod is gone first. Missing = skipped."""
        ...

    async def get_status(self, namespace: str, name: str) -> PodStatus: ...

    async def read_instance_logs(self, namespace: str, name: str, *, tail_lines: int) -> str:
        """Read the last tail_lines lines of the instance container log; allowed on the request
        path, the caller must authenticate and rate-limit."""
        ...

    async def list_instance_pods(self) -> list[PodStatus]:
        """List the status of managed Pods in the tenant namespaces, including Pods of managed
        Jobs."""
        ...

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        """List the (namespace, instance name) of every tenant instance's Service / HTTPRoute,
        aliases
        merged; for orphan endpoint cleanup."""
        ...

    async def used_node_ports(self) -> set[int]:
        """NodePorts held by every Service in the cluster, non-platform objects included."""
        ...

    async def ensure_data_disk(self, namespace: str, name: str, size_gb: int) -> None:
        """Create or grow the data-disk PVC (idempotent): create when missing, grow when smaller.
        The PVC capacity is the hard quota, nothing else is applied. An unsupported grow raises for
        the outbox to retry."""
        ...

    async def delete_data_disk(self, namespace: str, name: str) -> None:
        """Delete the data-disk PVC (reclaimPolicy=Delete, the CSI destroys the subvolume).
        Missing PVC or vanished ns count as success."""
        ...

    async def list_nodes(self, include_unlabeled: bool = False) -> list["NodeInfo"]:
        """Node view; by default only nodes with the pool label (POOL_NODE_LABEL),
        include_unlabeled=True adds unlabeled nodes."""
        ...

    async def set_node_labels(self, node_name: str, labels: dict[str, str | None]) -> None:
        """merge-patch node labels (the patrol converges superdl.io/gpu-model, pool switches
        converge
        the pool and operand labels).
        None = delete the key (native merge-patch semantics). Idempotent."""
        ...

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        """Create an image prewarm Job on the node and return at once (completion is converged by
        the
        patrol); skipped when the name exists."""
        ...

    async def get_prewarm_status(self, node_name: str, image_ref: str) -> "PrewarmJobStatus":
        """Status of the prewarm Job for that (node, image)."""
        ...

    async def delete_prewarm_job(self, node_name: str, image_ref: str) -> None:
        """Clean up the prewarm Job (reclaimed after convergence; missing = skipped)."""
        ...

    async def probe_cluster(self) -> "ClusterProbe":
        """Read-only capability probe: version / distro / component presence / RuntimeClass /
        StorageClass / pool distribution."""
        ...

    async def probe_component_detail(self, key: str) -> "ComponentDetail":
        """Live deep probe of one health-check item (read-only): Pod-level failure reasons, recent
        warning events, certificate expiry.
        Unknown key or nothing to probe returns an empty result, never raises."""
        ...

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        """cordon (True) / uncordon (False). Idempotent: setting the same value again has no
        effect."""
        ...

    async def delete_node(self, node_name: str) -> None:
        """Node decommissioning: cordon, then delete the Node object; a missing node counts as
        success. Does not revoke the kubelet certificate
        (a control-plane action, see the nodes module runbook)."""
        ...

    async def cluster_dns_ip(self) -> str:
        """ClusterIP of the cluster DNS Service (kube-system/kube-dns)."""
        ...

    async def apply_netgw(self, spec: NetgwSpec) -> None:
        """Converge one gateway: spec Secret, Deployment, NetworkPolicy, SOCKS and forward
        Services, tenant-namespace ExternalNames and instance Services (organization gateways:
        the L3 headless Service and NetworkPolicy in each attached instance's namespace); objects
        of the gateway no longer wanted are deleted, in any namespace. Idempotent."""
        ...

    async def delete_netgw(self, gateway_uuid: str) -> None:
        """Delete every object of the gateway, in the gateway namespace and in every tenant
        namespace holding one of its Services; missing objects are ignored."""
        ...

    async def list_netgw(self) -> list[NetgwStatus]:
        """Every gateway Deployment in the gateway namespace with its Pod readiness."""
        ...

    async def read_netgw_logs(self, gateway_uuid: str, *, tail_lines: int) -> str:
        """Last tail_lines lines of the gateway container log; empty when no Pod exists. Allowed on
        the request path, the caller must authenticate and rate-limit."""
        ...


GPU_MODEL_NODE_LABEL = "superdl.io/gpu-model"
# The pool label lives under the node-restriction.kubernetes.io/ prefix: the kubelet cannot set it,
# only the platform SA can, through the allow-list of admission policy 3.
POOL_NODE_LABEL = "node-restriction.kubernetes.io/superdl-pool"
# Legacy key: deleted when pool_node_labels converges.
LEGACY_POOL_NODE_LABEL = "superdl.io/pool"
# infra criterion: the platform placement label or a control-plane role label is present; such
# nodes are excluded from unenrolled isolation.
INFRA_NODE_LABEL = "node-restriction.kubernetes.io/superdl-infra"
INFRA_ROLE_LABELS = (
    "node-role.kubernetes.io/control-plane",
    "node-role.kubernetes.io/master",
    "node-role.kubernetes.io/etcd",
)
GPU_WORKLOAD_CONFIG_LABEL = "nvidia.com/gpu.workload.config"
GPU_DEPLOY_DEVICE_PLUGIN_LABEL = "nvidia.com/gpu.deploy.device-plugin"
MANAGED_LABEL = "superdl.io/managed"
INSTANCE_LABEL = "superdl.io/instance"
JOB_NAME_LABEL = "batch.kubernetes.io/job-name"


@dataclass(frozen=True)
class NodeInfo:
    """Admin node view."""

    name: str
    pool_label: str
    gpu_total: int
    gpu_used: int
    status: str
    vcpu: int = 0
    mem_gb: int = 0
    disk_gb: int = 0
    gpu_model_label: str = ""
    model_label_current: str = ""
    driver_version_label: str = ""
    cuda_version_label: str = ""
    infra: bool = False
