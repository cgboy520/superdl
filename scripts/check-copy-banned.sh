#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import json, pathlib, re, sys

BANNED = ["智能", "强大", "轻松", "一键", "全方位", "高效", "极速", "助力", "赋能", "颠覆", "极致"]
ALLOW = re.compile(r"一键(加入|添加)")


def hits(text: str) -> list[str]:
    stripped = ALLOW.sub("", text)
    return [w for w in BANNED if w in stripped]


targets = [
    "apps/web/src/locales/zh-CN/web.json",
    "apps/admin/src/locales/zh-CN/admin.json",
    "packages/ui/locales/zh-CN/shared.json",
    "packages/ui/locales/zh-CN/errors.json",
]
fail = []
def walk(o, path, file):
    for k, v in o.items():
        p = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            walk(v, p, file)
        elif isinstance(v, str):
            for w in hits(v):
                fail.append((file, p, w))

for f in targets:
    walk(json.loads(pathlib.Path(f).read_text()), "", f)

msgs = pathlib.Path("apps/api/app/core/messages.py").read_text()
for m in re.finditer(r'"[^"\n]*"', msgs):
    for w in hits(m.group(0)):
        fail.append(("apps/api/app/core/messages.py", m.group(0)[:40], w))

if fail:
    for file, where, w in fail:
        print(f"禁词「{w}」: {file} :: {where}")
    sys.exit(1)
print("banned-words check: clean")
PY
