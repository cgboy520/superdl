#!/usr/bin/env bash
# Guards against Chinese text outside the places where it belongs.
#   default        frontend source (apps/admin/src, apps/web/src) — the mode `pnpm copy-check` runs
#   --scope repo   every tracked source / doc file. Permanently exempt paths are listed in
#                  REPO_CJK_EXEMPT, a line ending in `cjk-ok` is skipped (regulatory names, quoted
#                  headings), and REPO_CJK_ALLOW is the shrinking list of path prefixes still waiting
#                  for translation: each translation PR removes its prefixes, nothing is ever added.
set -euo pipefail
cd "$(dirname "$0")/.."

scope="frontend"
if [[ "${1:-}" == "--scope" ]]; then scope="${2:-}"; fi

if [[ "$scope" == "frontend" ]]; then
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
fi

[[ "$scope" == "repo" ]] || { echo "usage: $0 [--scope repo]" >&2; exit 2; }

# Chinese is allowed here by design (UI locales, generated types/clients, frozen migrations,
# legal preset text, zh-CN e2e selectors).
REPO_CJK_EXEMPT=(
  "*/locales/*"
  "apps/*/src/types/resources.d.ts"
  "packages/api-client/src/generated/*"
  "apps/api/alembic/versions/*"
  "apps/api/tests/legal_preset.py"
  "e2e/tests/*"
)

# Still untranslated. Remove a prefix in the PR that translates it; never add one.
REPO_CJK_ALLOW=(
  ".github/"
  "CLAUDE.md"
  "apps/admin/src/"
  "apps/api/app/export_openapi.py"
  "apps/api/app/main.py"
  "apps/api/app/models_registry.py"
  "apps/api/app/modules/account/"
  "apps/api/app/modules/adminapi/"
  "apps/api/app/modules/billing/"
  "apps/api/app/modules/legal/"
  "apps/api/app/modules/notify/"
  "apps/api/app/modules/tickets/"
  "apps/api/app/wiring.py"
  "apps/api/app/workers/"
  "apps/api/pyproject.toml"
  "apps/api/scripts/"
  "apps/api/tests/"
  "apps/web/"
  "deploy/"
  "e2e/playwright.config.ts"
  "packages/api-client/src/"
  "packages/ui/src/"
  "scripts/"
)

CJK_EXEMPT="$(printf '%s\n' "${REPO_CJK_EXEMPT[@]}")" \
CJK_ALLOW="$(printf '%s\n' "${REPO_CJK_ALLOW[@]}")" \
python3 - <<'PY'
import fnmatch, os, re, subprocess, sys

EXTS = (".md", ".py", ".ts", ".tsx", ".sh", ".bats", ".yml", ".yaml", ".j2", ".conf", ".toml", ".html", ".css")
# Han ideographs, CJK symbols/punctuation (、。「」…) and fullwidth forms (,:();).
CJK = re.compile(r"[一-鿿　-〿＀-￯]")
exempt = [p for p in os.environ["CJK_EXEMPT"].splitlines() if p]
allow = [p for p in os.environ["CJK_ALLOW"].splitlines() if p]

files = [f for f in subprocess.check_output(["git", "ls-files"], text=True).split("\n") if f.endswith(EXTS)]
hits: list[str] = []
waiting: set[str] = set()
for path in files:
    if any(fnmatch.fnmatch(path, pat) for pat in exempt):
        continue
    allowed = any(path.startswith(prefix) for prefix in allow)
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except (UnicodeDecodeError, OSError):
        continue
    for no, line in enumerate(lines, 1):
        if "cjk-ok" in line or not CJK.search(line):
            continue
        if allowed:
            waiting.add(path)
            break
        hits.append(f"{path}:{no}: {line.strip()[:100]}")
if waiting:
    print(f"repo CJK gate: {len(waiting)} file(s) under REPO_CJK_ALLOW still contain Chinese (translation pending)")
if hits:
    print("repo CJK gate: Chinese text outside the allowed places (translate it, or end the line with `cjk-ok` for a regulatory name):")
    print("\n".join(hits))
    sys.exit(1)
print("repo CJK gate: clean")
PY
