"""渲染平台会生成的 Pod / Job(租户实例五种形态、数据盘擦除 Job、配额 Job、预热 Job)成清单目录,
供 CI 在准入策略生效的集群上 `kubectl create --dry-run=server` 对账。
挂了说明:平台自己生成的对象会被自家 VAP 拒(如擦除 Job 漏了 hostUsers)。

用法: uv run python scripts/render_admission_probes.py <输出目录>
输出:<name>.yaml(对象)与 <name>.as(dry-run 时 --as 的身份;空 = 当前身份)。
"""

import pathlib
import sys
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # 允许 scripts/ 直跑

import yaml
from kubernetes import client

from app.core.gpu_adapter import POOL_HAMI, POOL_KATA, POOL_MIG, build_gpu_request
from app.core.k8s.base import InstancePodSpec
from app.core.k8s.real import (
    build_instance_pod,
    build_prewarm_job,
)

TENANT_NS = "tenant-probe"
PLATFORM_NS = "superdl"
SA = "system:serviceaccount:superdl:"
JOB_CONTROLLER = "system:serviceaccount:kube-system:job-controller"


def _instance_spec(name: str, gpu_req: object) -> InstancePodSpec:
    req = gpu_req  # GpuRequest
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
    """Job 的 Pod 模板 → 独立 Pod(job-controller 派生 Pod 的准入面)。"""
    tpl = job.spec.template
    return client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=tpl.metadata.labels),
        spec=tpl.spec,
    )


def main(out_dir: pathlib.Path) -> None:
    api = client.ApiClient()
    out_dir.mkdir(parents=True, exist_ok=True)
    objects: list[tuple[str, object, str]] = []  # (name, obj, as-user)

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

    # 挂数据盘的实例形态:租户 ns 内唯一会引用 PVC 的 Pod(旧的 wipe Job 已随一盘一 PVC 取消)
    disk_spec = _instance_spec("instance-with-disk", shapes["instance-hami-k3s"])
    disk_spec = replace(disk_spec, data_disk_pvc="disk-probe")
    objects.append(
        (
            "instance-with-disk",
            build_instance_pod(disk_spec),
            SA + "superdl-tenant-mgr",
        )
    )

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

    for name, obj, as_user in objects:
        # 构造器不写 apiVersion/kind(client 按端点补),清单文件必须带
        if isinstance(obj, client.V1Pod):
            obj.api_version, obj.kind = "v1", "Pod"
        elif isinstance(obj, client.V1Job):
            obj.api_version, obj.kind = "batch/v1", "Job"
        data = api.sanitize_for_serialization(obj)
        (out_dir / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        (out_dir / f"{name}.as").write_text(as_user)
    print(f"rendered {len(objects)} probes into {out_dir}")  # noqa: T201


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)  # noqa: T201
        sys.exit(2)
    main(pathlib.Path(sys.argv[1]))
