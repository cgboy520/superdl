#!/usr/bin/env bash
# 「无手写 URL 绕过」闸门:apps/*/src 内禁止裸 fetch("...") / customFetch / axios。
# 数据访问一律走 @superdl/api-client 的生成 fetcher(唯一例外:生成物不在此目录)。
# 白名单机制:行内注释 no-handwritten-url(仅允许测试/mock 文件使用,并在 PR 中说明)。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
while IFS= read -r file; do
  # grep -P:Perl 正则;\b 防 refetch( 误命中
  if grep -P -n '(?<![\w.])fetch\s*\(\s*["'"'"'`]|customFetch\s*\(|\baxios\b' "$file" \
    | grep -v "no-handwritten-url"; then
    echo "手写 URL 绕过: $file"
    fail=1
  fi
done < <(find apps/admin/src apps/web/src -name "*.tsx" -o -name "*.ts" \
  | grep -v "routeTree.gen")

exit $fail
