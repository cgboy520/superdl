"""Export the instance Pod, the prewarm Job and its Pod template, and the private-network gateway
objects as admission probe manifests, plus deny-*.yaml variants of an L3-attached instance Pod the
tenant Pod baseline policy must refuse.

Usage: uv run python scripts/render_admission_probes.py <output dir>
Output: <name>.yaml (object) and <name>.as (the --as identity for dry-run; empty = current
identity).
"""

import copy
import pathlib
import sys
from collections.abc import Callable
from dataclasses import replace
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import yaml
from kubernetes import client

from app.core.gpu_adapter import POOL_HAMI, POOL_KATA, POOL_MIG, build_gpu_request
from app.core.k8s.base import (
    NETGW_NAMESPACE,
    PRIVATE_CIDRS,
    InstancePodSpec,
    NetgwAttach,
    NetgwInstanceForward,
    NetgwIntranetForward,
    NetgwSpec,
)
from app.core.k8s.netgw import (
    build_netgw_deployment,
    build_netgw_netpol,
    build_netgw_secret,
    build_netgw_services,
    build_tenant_services,
)
from app.core.k8s.real import (
    build_instance_pod,
    build_prewarm_job,
)

TENANT_NS = "tenant-probe"
PLATFORM_NS = "superdl"
SA = "system:serviceaccount:superdl:"
JOB_CONTROLLER = "system:serviceaccount:kube-system:job-controller"
REPLICASET_CONTROLLER = "system:serviceaccount:kube-system:replicaset-controller"
NETGW_PROBE = NetgwSpec(
    uuid="0" * 32,
    tenant_namespace=TENANT_NS,
    socks_name="probe",
    image="registry.invalid/superdl/superdl-netgw@sha256:" + "0" * 64,
    spec_json="{}",
    egress_ports=(("udp", 51820),),
    intranet=(NetgwIntranetForward(forward_id=1, name="nas", port=2049, listen_port=20000),),
    instance=(NetgwInstanceForward(forward_id=2, instance_name="probe", port=22),),
    bandwidth_mbps=100,
    image_pull_secret="superdl-registry-pull",
    l3=True,
)
NETGW_ATTACH_PROBE = NetgwAttach(
    gateway_uuid="0" * 32, image=NETGW_PROBE.image, config_json='{"version": 1}'
)


def _instance_spec(name: str, gpu_req: object) -> InstancePodSpec:
    req = gpu_req
    return InstancePodSpec(
        namespace=TENANT_NS,
        name=name,
        image="registry.invalid/probe:0",
        gpu_resources=req.resources,  # type: ignore[attr-defined]
        runtime_class=req.runtime_class,  # type: ignore[attr-defined]
        host_users=req.host_users,  # type: ignore[attr-defined]
        vcpu=1,
        mem_gb=1,
        disk_gb=1,
        ssh_node_port=31999,
        jupyter_host=f"{name}.app.example.invalid",
        secret_env={"JUPYTER_TOKEN": "probe"},
        node_selector=req.node_selector,  # type: ignore[attr-defined]
        scheduler_name=req.scheduler_name,  # type: ignore[attr-defined]
        annotations=dict(req.annotations),  # type: ignore[attr-defined]
        image_pull_secret="superdl-registry-pull",
    )


def _pod_from_job(job: client.V1Job, name: str, namespace: str) -> client.V1Pod:
    """Turn the Job's Pod template into a standalone Pod."""
    tpl = job.spec.template
    return client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=tpl.metadata.labels),
        spec=tpl.spec,
    )


def _netgw_objects() -> list[tuple[str, object, str]]:
    """Gateway objects as tenant-mgr writes them, plus the Pod the ReplicaSet controller creates
    from the Deployment template (admission policy 4 judges that Pod)."""
    mgr = SA + "superdl-tenant-mgr"
    deployment = build_netgw_deployment(NETGW_PROBE)
    tpl = deployment.spec.template  # type: ignore[union-attr]
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name="netgw-probe", namespace=NETGW_NAMESPACE, labels=tpl.metadata.labels
        ),
        spec=tpl.spec,
    )
    out: list[tuple[str, object, str]] = [
        ("netgw-deployment", deployment, mgr),
        ("netgw-pod", pod, REPLICASET_CONTROLLER),
        ("netgw-secret", build_netgw_secret(NETGW_PROBE), mgr),
        ("netgw-netpol", build_netgw_netpol(NETGW_PROBE, except_cidrs=PRIVATE_CIDRS), mgr),
    ]
    services = build_netgw_services(NETGW_PROBE) + build_tenant_services(NETGW_PROBE)
    out.extend((f"netgw-service-{i}", svc, mgr) for i, svc in enumerate(services))
    return out


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    """Mutator setting a dotted path below the Pod dict (`spec.initContainers.0.image`)."""

    def apply(pod: dict[str, Any]) -> None:
        *head, last = path.split(".")
        node: Any = pod
        for key in head:
            node = node[int(key)] if key.isdigit() else node.setdefault(key, {})
        node[int(last) if last.isdigit() else last] = value

    return apply


def _attach_as_container(pod: dict[str, Any]) -> None:
    pod["spec"]["containers"].append(pod["spec"].pop("initContainers")[0])


def _deny_probes(attached: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Variants of the attached instance Pod, each breaking one rule of the tenant Pod baseline
    policy (the netgw-attach exception, and the PSA baseline controls the policy enforces)."""
    init = "spec.initContainers.0"
    cases: dict[str, Callable[[dict[str, Any]], None]] = {
        "attach-as-container": _attach_as_container,
        "attach-other-image": _set(f"{init}.image", "busybox:1.36"),
        "attach-extra-capability": _set(
            f"{init}.securityContext.capabilities.add", ["NET_ADMIN", "SYS_ADMIN"]
        ),
        "attach-writable-root": _set(f"{init}.securityContext.readOnlyRootFilesystem", False),
        "attach-env": _set(f"{init}.env", [{"name": "X", "value": "1"}]),
        "attach-sidecar": _set(f"{init}.restartPolicy", "Always"),
        "attach-extra-mount": _set(
            f"{init}.volumeMounts", [{"name": "instance-disk", "mountPath": "/root"}]
        ),
        "netadmin-other-init": _set(f"{init}.name", "setup"),
        "workspace-netadmin": _set(
            "spec.containers.0.securityContext.capabilities.add", ["NET_ADMIN"]
        ),
        "host-port": _set("spec.containers.0.ports", [{"containerPort": 8080, "hostPort": 8080}]),
        "proc-unmasked": _set("spec.containers.0.securityContext.procMount", "Unmasked"),
        "sysctl": _set(
            "spec.securityContext.sysctls", [{"name": "net.ipv4.ip_forward", "value": "0"}]
        ),
        "apparmor-unconfined": _set(
            "spec.containers.0.securityContext.appArmorProfile", {"type": "Unconfined"}
        ),
        "selinux-user": _set("spec.securityContext.seLinuxOptions", {"user": "system_u"}),
        "selinux-type": _set("spec.containers.0.securityContext.seLinuxOptions", {"type": "spc_t"}),
    }
    out: list[tuple[str, dict[str, Any]]] = []
    for name, mutate in cases.items():
        pod = copy.deepcopy(attached)
        pod["metadata"]["name"] = f"deny-{name}"
        mutate(pod)
        out.append((f"deny-{name}", pod))
    return out


def main(out_dir: pathlib.Path) -> None:
    api = client.ApiClient()
    out_dir.mkdir(parents=True, exist_ok=True)
    objects: list[tuple[str, object, str]] = []

    shapes = {
        "instance-cpu": build_gpu_request(
            pool_label=POOL_HAMI, gpu_count=0, gpu_cores_pct=100, vram_gb=0, mig_profile=None
        ),
        "instance-hami-k3s": build_gpu_request(
            pool_label=POOL_HAMI,
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=32,
            mig_profile=None,
            gpu_model="GB10",
            distro="k3s",
        ),
        "instance-hami-rke2": build_gpu_request(
            pool_label=POOL_HAMI,
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=32,
            mig_profile=None,
            distro="rke2",
        ),
        "instance-mig": build_gpu_request(
            pool_label=POOL_MIG, gpu_count=1, gpu_cores_pct=100, vram_gb=20, mig_profile="1g.20gb"
        ),
        "instance-kata": build_gpu_request(
            pool_label=POOL_KATA, gpu_count=1, gpu_cores_pct=100, vram_gb=80, mig_profile=None
        ),
    }
    for name, req in shapes.items():
        objects.append(
            (name, build_instance_pod(_instance_spec(name, req)), SA + "superdl-tenant-mgr")
        )

    disk_spec = _instance_spec("instance-with-disk", shapes["instance-hami-k3s"])
    disk_spec = replace(disk_spec, data_disk_pvc="disk-probe")
    objects.append(
        (
            "instance-with-disk",
            build_instance_pod(disk_spec),
            SA + "superdl-tenant-mgr",
        )
    )

    attached_spec = replace(
        _instance_spec("instance-l3", shapes["instance-hami-k3s"]), netgw=NETGW_ATTACH_PROBE
    )
    attached = build_instance_pod(attached_spec)
    objects.append(("instance-l3", attached, SA + "superdl-tenant-mgr"))

    prewarm_job = build_prewarm_job(
        PLATFORM_NS,
        "prewarm-probe",
        "probe-node",
        "registry.invalid/probe:0",
        "superdl-registry-pull",
    )
    objects.append(("prewarm-job", prewarm_job, SA + "superdl-prewarm"))
    objects.append(
        ("prewarm-pod", _pod_from_job(prewarm_job, "prewarm-probe", PLATFORM_NS), JOB_CONTROLLER)
    )

    objects.extend(_netgw_objects())

    kinds = {
        client.V1Pod: ("v1", "Pod"),
        client.V1Job: ("batch/v1", "Job"),
        client.V1Deployment: ("apps/v1", "Deployment"),
        client.V1Secret: ("v1", "Secret"),
        client.V1Service: ("v1", "Service"),
        client.V1NetworkPolicy: ("networking.k8s.io/v1", "NetworkPolicy"),
    }
    rendered: list[tuple[str, dict[str, Any], str]] = []
    for name, obj, as_user in objects:
        obj.api_version, obj.kind = kinds[type(obj)]  # type: ignore[attr-defined]
        rendered.append((name, api.sanitize_for_serialization(obj), as_user))
    attached_dict = next(d for n, d, _ in rendered if n == "instance-l3")
    rendered.extend(
        (name, pod, SA + "superdl-tenant-mgr") for name, pod in _deny_probes(attached_dict)
    )
    for name, data, as_user in rendered:
        (out_dir / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        (out_dir / f"{name}.as").write_text(as_user)
    print(f"rendered {len(rendered)} probes into {out_dir}")  # noqa: T201


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)  # noqa: T201
        sys.exit(2)
    main(pathlib.Path(sys.argv[1]))
