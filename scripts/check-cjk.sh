#!/usr/bin/env bash
# CJK 残留闸门:剥注释后在两端源码里查汉字。
# 豁免:locales/、测试、生成物、types/、web 的 legal.*
# (平台配置字段名 / 提供方名 / 风险复述 / 策略参数名已进 locales,不再有常量块豁免)
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
while IFS= read -r file; do
  # 剥行注释与块注释(近似)
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
