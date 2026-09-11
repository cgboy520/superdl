#!/usr/bin/env bash
# CJK 残留闸门:剥注释后在两端源码里查汉字。
# 豁免:locales/、测试、生成物、types/、platform.tsx 的 FIELD_LABELS/PROVIDER_LABELS/RISK_OFF 常量块、
# settings.tsx 的 POLICY_LABELS、web 的 legal.*
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
while IFS= read -r file; do
  # 剥行注释与块注释(近似)
  if python3 - "$file" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
# 运营域术语常量块豁免:先于注释剥离执行
for name in ("POLICY_LABELS", "FIELD_LABELS", "PROVIDER_LABELS", "RISK_OFF"):
    src = re.sub(r"const %s[\s\S]*?\n\};" % name, "", src)
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
