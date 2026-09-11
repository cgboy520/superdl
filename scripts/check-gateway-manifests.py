#!/usr/bin/env python3
"""`deploy/app/k8s/04-gateway.yaml` 的 CRD schema 校验(CI 调用):schema 取自 helmfile 钉死的
Envoy Gateway chart(CHART_VERSION)。挂了说明:清单里有字段名/取值不被 apiserver 接受。

依赖 PyYAML、jsonschema(不进仓库依赖树)。
用法: uv run --with pyyaml --with jsonschema python3 scripts/check-gateway-manifests.py
"""

import pathlib
import subprocess
import sys
import tempfile

import jsonschema
import yaml

# 必须与 deploy/cluster/helmfile.yaml.gotmpl 里 envoy-gateway release 的 version 一致
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
                # 内置资源由 kubeconform 管,这里只认 CRD
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
