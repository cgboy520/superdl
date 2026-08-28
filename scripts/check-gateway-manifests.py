#!/usr/bin/env python3
"""北向入口清单的 CRD schema 校验(CI 调用)。

`deploy/app/k8s/04-gateway.yaml` 里的 7 种对象全是 CRD,kubeconform 的内置 schema 没有它们
只能 -skip 掉,而这个文件恰是「写错了不报错、只是策略静默失效」的重灾区(管理端源 IP
白名单、边缘限流、Jupyter 的 WebSocket 超时都在里面)。

schema 直接取自 helmfile 钉死的那版 Envoy Gateway chart(CHART_VERSION),校验的就是集群里
真正装着的那套 CRD。挂了说明:清单里有字段名/取值不被 apiserver 接受,apply 会被拒
(可能只拒其中一个对象,其余照常生效)。

依赖(不进仓库依赖树,CI 用 `uv run --with` 临时装):PyYAML、jsonschema。
用法: uv run --with pyyaml --with jsonschema python3 scripts/check-gateway-manifests.py
"""

import pathlib
import subprocess
import sys
import tempfile

import jsonschema
import yaml

# 必须与 deploy/cluster/helmfile.yaml.gotmpl 里 envoy-gateway release 的 version 一致。
# 对不上就等于拿另一版的 schema 校验现网清单,校验通过也不说明什么。
CHART_VERSION = "v1.9.0"
CHART = "oci://docker.io/envoyproxy/gateway-helm"
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [REPO / "deploy/app/k8s/04-gateway.yaml"]


def _load_schemas(crd_dir: pathlib.Path) -> dict[tuple[str, str], dict]:
    """(apiVersion, kind) → openAPIV3Schema。chart 把两组 CRD 都放在 crds/ 下。"""
    out: dict[tuple[str, str], dict] = {}
    for f in sorted(crd_dir.rglob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if not doc or doc.get("kind") != "CustomResourceDefinition":
                continue
            group = doc["spec"]["group"]
            kind = doc["spec"]["names"]["kind"]
            for ver in doc["spec"]["versions"]:
                if "schema" in ver:
                    out[(f"{group}/{ver['name']}", kind)] = ver["schema"]["openAPIV3Schema"]
    return out


def _strip_kube(node):
    """剥掉 x-kubernetes-*:CEL 校验(x-kubernetes-validations)与 int-or-string
    等扩展关键字不是 JSON Schema,jsonschema 认不了。剥掉后仍能校验字段名、类型、
    枚举与 pattern —— 静默失效的那类错误全在这几项里。"""
    if isinstance(node, dict):
        return {k: _strip_kube(v) for k, v in node.items() if not k.startswith("x-kubernetes")}
    if isinstance(node, list):
        return [_strip_kube(v) for v in node]
    return node


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                ["helm", "pull", CHART, "--version", CHART_VERSION, "--untar", "-d", tmp],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"拉取 {CHART}:{CHART_VERSION} 失败:{exc}", file=sys.stderr)
            return 1
        schemas = _load_schemas(pathlib.Path(tmp) / "gateway-helm" / "charts" / "crds" / "crds")

    problems: list[str] = []
    checked = 0
    for target in TARGETS:
        for doc in yaml.safe_load_all(target.read_text()):
            if not doc:
                continue
            key = (doc.get("apiVersion", ""), doc.get("kind", ""))
            schema = schemas.get(key)
            if schema is None:
                # 内置资源(Service/ConfigMap…)由 kubeconform 管,这里只认 CRD
                continue
            checked += 1
            name = doc.get("metadata", {}).get("name", "?")
            try:
                jsonschema.validate(doc, _strip_kube(schema))
            except jsonschema.ValidationError as exc:
                where = "/".join(str(p) for p in exc.absolute_path)
                problems.append(f"{target.name} {key[1]}/{name} 的 {where}: {exc.message}")

    if problems:
        print(f"网关清单 schema 校验失败({len(problems)} 处):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"网关清单 schema 校验通过(Gateway API/Envoy Gateway {CHART_VERSION},{checked} 个对象)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
