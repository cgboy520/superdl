#!/usr/bin/env python3
"""Validate the gateway manifests against the CRD schemas of the Envoy Gateway chart pinned by CHART_VERSION.

Target: deploy/app/k8s/04-gateway.yaml; objects without a matching schema are skipped.
Runtime dependencies: helm, PyYAML, jsonschema.
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
    """Read the CRDs in the directory and return openAPIV3Schema keyed by (apiVersion, kind)."""
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
    """Recursively remove dictionary keys starting with x-kubernetes."""
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
            print(f"pulling {CHART}:{CHART_VERSION} failed: {exc}", file=sys.stderr)
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
                problems.append(f"{target.name} {key[1]}/{name} at {where}: {exc.message}")

    if problems:
        print(f"gateway manifest schema validation failed ({len(problems)} problem(s)):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"gateway manifest schema validation passed (Gateway API/Envoy Gateway {CHART_VERSION}, {checked} objects)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
