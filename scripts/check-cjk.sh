#!/usr/bin/env bash
# CJK 残留闸门:剥注释后在源码里查汉字。web 由 i18next-cli lint(AST 级)守护,本脚本守 admin
# (其 lint 无法表达 platform.tsx 渠道常量表的豁免)。豁免:locales/ 目录、测试、生成物、
# platform.tsx(中国渠道字段表/指引,决策不译)、settings.tsx 的 POLICY_LABELS 常量(同性质)。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
while IFS= read -r file; do
  # 剥行注释与块注释(近似;JSX 注释 {/* */} 同被块注释规则覆盖)
  if python3 - "$file" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
# settings.tsx 的 POLICY_LABELS 常量块豁免(运营域术语,决策不译)——先于注释剥离执行
src = re.sub(r"const POLICY_LABELS[\s\S]*?\n\};", "", src)
src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
src = re.sub(r"(?m)^\s*//.*$", "", src)
src = re.sub(r"(?m)\s//[^\"'`]*$", "", src)
sys.exit(1 if re.search(r"[一-鿿]", src) else 0)
PY
  then :; else
    echo "CJK 残留: $file"
    fail=1
  fi
done < <(find apps/admin/src -name "*.tsx" -o -name "*.ts" | grep -v "locales/\|.test.\|routeTree.gen\|types/\|/platform.tsx")

exit $fail
