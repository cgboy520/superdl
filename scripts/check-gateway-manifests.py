#!/usr/bin/env python3
"""用 CHART_VERSION 指定的 Envoy Gateway chart 中的 CRD schema 校验网关清单。

目标:deploy/app/k8s/04-gateway.yaml;无匹配 schema 的对象跳过。
运行依赖:helm、PyYAML、jsonschema。
"""

import pathlib
import subprocess
import sys
import tempfile

import jsonschema
import yaml

CHART_VERSION = "v1.9.0"
CHART = "oci://docker.io/envoyproxy/gateway-helm"
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [REPO / "deploy/app/k8s/04-gateway.yaml"]


def _load_schemas(crd_dir: pathlib.Path) -> dict[tuple[str, str], dict]:
    """读取目录内 CRD,按 (apiVersion, kind) 返回 openAPIV3Schema。"""
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
    """递归移除以 x-kubernetes 开头的字典键。"""
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
