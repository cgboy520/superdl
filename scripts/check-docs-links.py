#!/usr/bin/env python3
"""文档引用检查(stdlib 零依赖):Markdown 里的相对链接与反引号仓库路径必须真实存在。

它挂了说明什么坏了:某份文档引用的文件/目录已被改名或删除(或链接写错),读者会被
带到不存在的地方。文档与代码同提交是本仓规范(CLAUDE.md「提交约定」),本脚本把
「引用未随改动更新」变成闸门,而不是靠人记得。

检查范围:仓库内全部 *.md(排除 node_modules/.venv/.git/dist/generated)。
检查两类引用:
1. Markdown 链接 `[text](target)`:target 非 http(s)/mailto/纯锚点时,按所在文件目录解析,
   去掉 `#anchor` 后必须存在。
2. 反引号内的仓库路径:形如 `deploy/cluster/README.md`、`app/core/money.py`、
   `docs/reference/`、`apps/api/alembic/versions/*` —— 依次尝试按「文档所在目录 →
   仓库根 → apps/api」解析,任一命中即通过;只检查首段是仓库顶层目录
   (apps/packages/deploy/docs/e2e/scripts/.github)或 apps/api 内部目录
   (app/alembic/tests/scripts)的 token。路径可带通配符(glob)。
   不含 `/` 的短名、URL、命令行片段不在检查范围。

放行:含 `<`/`>`/`{`/`}`/`$` 的占位路径(如 `deploy/k8s/<file>`)不检查。

用法: python3 scripts/check-docs-links.py            # 检查全仓
      python3 scripts/check-docs-links.py a.md b.md  # 只检查给定文件
退出码:0 通过;1 有断链(逐条打印 file:line: 说明)。
"""

# ruff: noqa: T201  # 闸门脚本以 stdout 报告结果
from __future__ import annotations

import glob
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXCLUDE_DIRS = {"node_modules", ".venv", ".git", "dist", "generated", ".turbo", ".pytest_cache"}

TOP_LEVEL = ("apps", "packages", "deploy", "docs", "e2e", "scripts", ".github")
API_LOCAL = ("app", "alembic", "tests", "scripts")

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
PATH_LIKE_RE = re.compile(r"^[A-Za-z0-9_.@*-]+(?:/[A-Za-z0-9_.@*-]+)*/?$")
PLACEHOLDER_CHARS = set("<>{}$")


def iter_markdown_files() -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            if name.lower().endswith(".md"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _exists_glob(pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return bool(glob.glob(pattern, recursive=True))
    return os.path.exists(pattern)


def check_link(target: str, base_dir: str) -> str | None:
    """相对链接:按文件所在目录解析。返回错误说明或 None。"""
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    path = target.split("#", 1)[0]
    if not path or any(ch in path for ch in PLACEHOLDER_CHARS):
        return None
    resolved = os.path.normpath(os.path.join(base_dir, path))
    if not _exists_glob(resolved):
        return f"链接目标不存在:{target}"
    return None


def check_code_path(token: str, base_dir: str) -> str | None:
    """反引号路径:按「文档目录 → 仓库根 → apps/api」依次解析。返回错误说明或 None。"""
    token = token.strip()
    if "/" not in token or not PATH_LIKE_RE.match(token):
        return None
    if any(ch in token for ch in PLACEHOLDER_CHARS):
        return None
    head = token.split("/", 1)[0]
    if head not in TOP_LEVEL and head not in API_LOCAL:
        return None
    bases = [base_dir, ROOT, os.path.join(ROOT, "apps", "api")]
    if any(_exists_glob(os.path.join(b, token)) for b in bases):
        return None
    return f"仓库路径不存在:`{token}`"


def check_file(path: str) -> list[str]:
    findings: list[str] = []
    base_dir = os.path.dirname(path)
    rel = os.path.relpath(path, ROOT)
    in_fence = False
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            for m in LINK_RE.finditer(line):
                err = check_link(m.group(1), base_dir)
                if err:
                    findings.append(f"{rel}:{lineno}: {err}")
            if in_fence:
                continue  # 代码块里是命令示例,不当路径引用检查
            for m in CODE_RE.finditer(line):
                err = check_code_path(m.group(1), base_dir)
                if err:
                    findings.append(f"{rel}:{lineno}: {err}")
    return findings


def main(argv: list[str]) -> int:
    files = [os.path.abspath(a) for a in argv[1:]] or iter_markdown_files()
    findings: list[str] = []
    for path in files:
        findings.extend(check_file(path))
    if not findings:
        print(f"文档引用检查通过({len(files)} 个文件)")
        return 0
    for line in findings:
        print(f"::error::{line}")
    print(f"共 {len(findings)} 处断链:修正引用,或引用已删除的文件时同步改文档")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
