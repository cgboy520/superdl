"""Offline command double for test_release.py; never contacts a cluster/registry."""

import json
import os
from pathlib import Path
import re
import sys

WRITERS = (
    "superdl-api", "superdl-worker", "superdl-worker-tenant-mgr",
    "superdl-worker-node-mgr", "superdl-worker-prewarm", "superdl-worker-disk-ops",
)
DIGESTS = dict(zip(("api", "web", "admin"), ("sha256:" + c * 64 for c in "abc")))
state_path = Path(os.environ["RELEASE_TEST_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
command = Path(sys.argv[0]).name
record = {"command": command, "args": args, "mutation": False}


def finish(output="", code=0):
    with Path(os.environ["RELEASE_TEST_LOG"]).open("a") as log:
        log.write(json.dumps(record) + "\n")
    state_path.write_text(json.dumps(state))
    if output:
        print(output)
    sys.exit(code)


def option(flag):
    return args[args.index(flag) + 1]


def documents(text):
    return re.split(r"^---\s*$", text, flags=re.M)


def field(text, key):
    match = re.search(r"^  " + key + r": (.+)$", text, re.M)
    return match[1] if match else ""


if command == "git":
    finish("git@github.com:example-owner/superdl.git")
if command == "curl":
    finish(code=int(state.get("smoke_fail", False)))
if command in ("crane", "docker"):
    ref = next(a for a in args if "/superdl-" in a)
    image = re.search(r"/superdl-(api|web|admin):", ref)[1]
    failed = state.get("digest_fail") == image
    # A failing resolver can still produce plausible stdout; it must be ignored.
    finish(DIGESTS[image] if not failed or state.get("digest_on_failure") else "", int(failed))
if command == "cosign":
    image = re.search(r"/superdl-(api|web|admin)@", args[-1])[1]
    finish(code=int(state.get("signature_fail") == image))
if command != "kubectl":
    finish("unexpected command", 97)
if args[:1] == ["-n"]:
    args = args[2:]
verb = args[0]

if verb == "kustomize":
    overlay = Path(args[1]) / "kustomization.yaml"
    text = overlay.read_text()
    root = Path(re.search(r"^  - (/.+)$", text, re.M)[1])
    counts = dict(re.findall(r"  - name: (\S+)\n    count: (\d+)", text))
    resources = re.findall(r"^  - (.+)$", (root / "kustomization.yaml").read_text(), re.M)
    rendered = []
    for resource in resources:
        for doc in documents((root / resource).read_text()):
            if "kind: Deployment\n" in doc and field(doc, "name") in counts:
                doc = re.sub(r"^  replicas: \d+$", "  replicas: " + counts[field(doc, "name")], doc, flags=re.M)
            rendered.append(doc)
    output = "\n---\n".join(rendered)
    if state.get("bad_manifest"):
        output = re.sub(r"postgres:18@sha256:[0-9a-f]+", "postgres:18", output)
    finish(output)

if verb == "get":
    kind = args[1]
    name = args[2] if len(args) > 2 else ""
    fmt = option("-o") if "-o" in args else ""
    if kind in ("validatingadmissionpolicybinding", "validatingadmissionpolicy"):
        finish("Deny" if kind.endswith("binding") else name, int(state.get("admission_fail", False)))
    if kind in ("namespace", "secret"):
        finish(name)
    if kind == "node":
        finish("False" if state.get("node_unready") else "True")
    if kind == "hpa":
        finish("Deployment " + state["hpa"] if state.get("hpa") else "")
    if kind == "jobs":
        finish("superdl-migrate-old  " if state.get("active_job") else "")
    if kind == "pods":
        selector = option("-l")
        if selector == "app=superdl-migrate":
            finish("pod/active-migration" if state.get("active_migration") else "")
        if selector != "app in (superdl-api,superdl-worker)":
            finish("wrong writer selector", 96)
        if "spec.nodeName" in fmt:
            finish("infra-node" if state["old_pods"] else "")
        finish("\n".join("pod/" + d + "-old" for d in state["old_pods"]))
    if kind == "replicasets":
        finish("\n".join(str(v) for v in state["deployments"].values()))
    if kind == "configmap":
        if name == "superdl-api-config":
            finish("")
        checkpoint = state.get("checkpoint")
        if fmt == "name":
            finish("configmap/" + name if checkpoint is not None else "")
        key = re.search(r"\.data\.([^}]+)", fmt)[1]
        finish(str(checkpoint.get(key, "")))
    if kind == "deployment":
        if name not in state["deployments"]:
            finish("")
        if "containers[0].image" in fmt:
            finish(state["images"].get(name, "old-image"))
        if "metadata.name" in fmt:
            finish(f"{name} {state['deployments'][name]} {str(state.get('paused', False)).lower()}")
        finish(str(state["deployments"][name]))

if verb in ("apply", "create") and "-f" in args:
    filename = Path(option("-f"))
    text = filename.read_text()
    record["images"] = re.findall(r"image: (\S+)", text)
    if "--dry-run=server" in args:
        finish(code=int(state.get("dryrun_fail") == filename.stem))
    record["mutation"] = True
    if any(state["deployments"].values()) or state["old_pods"]:
        finish("BUG: write before quiescence", 95)
    if verb == "create":
        state["migration_count"] = state.get("migration_count", 0) + 1
        state["migration_job"] = re.search(r"name: (superdl-migrate-\S+)", text)[1]
        finish()
    if filename.stem == "stopped":
        if not state.get("migrated"):
            finish("BUG: rollout before migration", 94)
        for doc in documents(text):
            name = field(doc, "name")
            if "kind: Deployment\n" in doc and name in WRITERS:
                state["deployments"][name] = int(field(doc, "replicas"))
                state["images"][name] = re.search(r"image: (\S+)", doc)[1]
        finish(code=int(state.get("apply_fail", False)))
    finish()

if verb == "create" and args[1] == "configmap":
    record["mutation"] = True
    if state.get("checkpoint") is not None:
        finish("checkpoint already exists", 1)
    state["checkpoint"] = {
        a.split("=", 2)[1]: int(a.split("=", 2)[2])
        for a in args if a.startswith("--from-literal=")
    }
    finish()
if verb == "delete" and args[1] == "configmap":
    record["mutation"] = True
    state["checkpoint"] = None
    finish()
if verb == "scale":
    record["mutation"] = True
    name = args[1].split("/")[1]
    replicas = int(next(a.split("=", 1)[1] for a in args if a.startswith("--replicas=")))
    if name not in state["deployments"]:
        finish(code=1)
    if "--current-replicas=0" in args and state["deployments"][name] != 0:
        finish(code=1)
    if replicas and not state.get("migrated"):
        finish("BUG: restart before migration success", 93)
    state["deployments"][name] = replicas
    finish()
if verb == "wait":
    if "--for=delete" in args:
        if state.get("stop_fail"):
            finish(code=1)
        state["old_pods"] = []
        if state.get("pods_disappear_before_wait"):
            finish(code=1)
        if state.get("node_lost_during_stop"):
            state["node_unready"] = True
        if state.get("controller_restart"):
            state["deployments"][WRITERS[-1]] = 1
        finish()
    if "--for=condition=complete" in args:
        if state.get("migration_fail"):
            finish(code=1)
        state["migrated"] = True
        finish()
if verb == "rollout":
    finish(code=int(state.get("rollout_fail", False) and any(state["deployments"].values())))
finish("unexpected kubectl invocation", 98)
