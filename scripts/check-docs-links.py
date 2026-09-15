#!/usr/bin/env python3
"""检查 Markdown 相对链接、代码围栏外的反引号仓库路径及告警 runbook_url。

用法:python3 scripts/check-docs-links.py [a.md b.md ...]。
无参数时扫描全仓文档与告警规则;有参数时只检查指定文档。
退出码:0 通过;1 有断链,逐条打印文件、行号与说明。
"""

from __future__ import annotations

import glob
import os
import re
import sys
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXCLUDE_DIRS = {"node_modules", ".venv", ".git", ".claude", ".trae", "dist", "generated", ".turbo", ".pytest_cache"}

TOP_LEVEL = ("apps", "packages", "deploy", "docs", "e2e", "scripts", ".github")
API_LOCAL = ("app", "alembic", "tests", "scripts")

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
PATH_LIKE_RE = re.compile(r"^[A-Za-z0-9_.@*-]+(?:/[A-Za-z0-9_.@*-]+)*/?$")
PLACEHOLDER_CHARS = set("<>{}$")
RUNBOOK_URL_RE = re.compile(
    r'runbook_url:\s*"?https?://github\.com/[^/\s"]+/[^/\s"]+/blob/[^/\s"]+/([^\s"#]+)(?:#([^\s"]+))?'
)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


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
                continue
            for m in CODE_RE.finditer(line):
                err = check_code_path(m.group(1), base_dir)
                if err:
                    findings.append(f"{rel}:{lineno}: {err}")
    return findings


def github_slug(heading: str) -> str:
    """GitHub 标题锚点:小写,去标点(保留字母数字含 CJK、空格、连字符、下划线),空格转连字符。"""
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s+", "-", text)


def heading_slugs(md_path: str) -> set[str]:
    slugs: set[str] = set()
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            m = HEADING_RE.match(line)
            if m:
                slugs.add(github_slug(m.group(1)))
    return slugs


def check_runbook_urls(deploy_dir: str = os.path.join(ROOT, "deploy")) -> list[str]:
    """告警规则里的 runbook_url:目标文件必须存在,锚点必须对得上标题。返回错误列表。"""
    findings: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(deploy_dir):
        for name in sorted(filenames):
            if not name.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT)
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    for m in RUNBOOK_URL_RE.finditer(line):
                        repo_path, anchor = m.group(1), m.group(2)
                        target = os.path.join(ROOT, repo_path)
                        if not os.path.isfile(target):
                            findings.append(f"{rel}:{lineno}: runbook_url 目标不存在:{repo_path}")
                            continue
                        if anchor:
                            anchor = urllib.parse.unquote(anchor)
                            if anchor not in heading_slugs(target):
                                findings.append(
                                    f"{rel}:{lineno}: runbook_url 锚点不存在:#{anchor}(目标 {repo_path})"
                                )
    return findings


def main(argv: list[str]) -> int:
    files = [os.path.abspath(a) for a in argv[1:]] or iter_markdown_files()
    findings: list[str] = []
    for path in files:
        findings.extend(check_file(path))
    if len(argv) == 1:
        findings.extend(check_runbook_urls())
    if not findings:
        print(f"文档引用检查通过({len(files)} 个文件)")
        return 0
    for line in findings:
        print(f"::error::{line}")
    print(f"共 {len(findings)} 处断链:修正引用,或引用已删除的文件时同步改文档")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
