#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
while IFS= read -r file; do
  if python3 - "$file" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
src = re.sub(r"(?m)^\s*//.*$", "", src)
src = re.sub(r"(?m)\s//[^\"'`]*$", "", src)
sys.exit(1 if re.search(r"[一-鿿]", src) else 0)
PY
  then :; else
    echo "CJK 残留: $file"
    fail=1
  fi
done < <(find apps/admin/src apps/web/src -name "*.tsx" -o -name "*.ts" \
  | grep -v "locales/\|.test.\|routeTree.gen\|types/\|/legal\.")

exit $fail
