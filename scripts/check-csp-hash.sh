#!/usr/bin/env bash
# CSP 内联脚本 hash 闸门:重算 apps/web/index.html 内联主题预置脚本的 sha256,与 deploy/app/security-headers-web-csp.conf 比对,
# 不一致即非零退出并打印新 hash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import base64
import hashlib
import re
import sys

html = open("apps/web/index.html", encoding="utf-8", newline="").read()
# 取无 src 的内联 <script>(唯一一枚)
blocks = re.findall(r"<script>([\s\S]*?)</script>", html)
if len(blocks) != 1:
    sys.exit(f"index.html 内联脚本数量异常: {len(blocks)}(期望 1)")
# CRLF 规范化为 LF 后计算 hash
digest = base64.b64encode(
    hashlib.sha256(blocks[0].replace("\r\n", "\n").encode("utf-8")).digest()
).decode()
csp = open("deploy/app/security-headers-web-csp.conf", encoding="utf-8").read()
if f"'sha256-{digest}'" in csp:
    print(f"CSP 内联脚本 hash 一致: sha256-{digest}")
else:
    print(f"::error::index.html 内联脚本 hash 与 CSP 不一致,请将 script-src 同步为 'sha256-{digest}'")
    sys.exit(1)
PY
