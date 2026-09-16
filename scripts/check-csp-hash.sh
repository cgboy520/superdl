#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import base64
import hashlib
import re
import sys

html = open("apps/web/index.html", encoding="utf-8", newline="").read()
blocks = re.findall(r"<script>([\s\S]*?)</script>", html)
if len(blocks) != 1:
    sys.exit(f"unexpected number of inline scripts in index.html: {len(blocks)} (expected 1)")
digest = base64.b64encode(
    hashlib.sha256(blocks[0].replace("\r\n", "\n").encode("utf-8")).digest()
).decode()
csp = open("deploy/app/security-headers-web-csp.conf", encoding="utf-8").read()
if f"'sha256-{digest}'" in csp:
    print(f"CSP inline script hash matches: sha256-{digest}")
else:
    print(f"::error::the index.html inline script hash differs from the CSP; set script-src to 'sha256-{digest}'")
    sys.exit(1)
PY
