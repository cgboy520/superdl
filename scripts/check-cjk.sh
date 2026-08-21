#!/usr/bin/env bash
# CJK 残留闸门:剥注释后在源码里查汉字。两端都查 —— i18next-cli lint 只查插值参数,
# 查不出裸字面量(侧栏 label、"费用明细" 这类曾整段漏翻)。
# 豁免:locales/ 目录、测试、生成物、types/(locale JSON 的类型声明)、
# platform.tsx(中国渠道字段表/指引,决策不译)、settings.tsx 的 POLICY_LABELS 常量(同性质)、
# web 的 legal.*(用户协议/隐私政策中文原文,待法务审定,不译)。
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
done < <(find apps/admin/src apps/web/src -name "*.tsx" -o -name "*.ts" \
  | grep -v "locales/\|.test.\|routeTree.gen\|types/\|/platform.tsx\|/legal\.")

exit $fail
