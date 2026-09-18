#!/usr/bin/env python3
"""Check Markdown relative links, backticked repository paths outside code fences and alert runbook_url values.

Usage: python3 scripts/check-docs-links.py [a.md b.md ...].
Without arguments it scans every document in the repository plus the alert rules; with arguments only the given documents.
Exit code: 0 = pass; 1 = broken references, each printed with file, line and explanation.
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
    """Relative link: resolved against the file's directory. Returns the error text or None."""
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    path = target.split("#", 1)[0]
    if not path or any(ch in path for ch in PLACEHOLDER_CHARS):
        return None
    resolved = os.path.normpath(os.path.join(base_dir, path))
    if not _exists_glob(resolved):
        return f"link target missing: {target}"
    return None


def check_code_path(token: str, base_dir: str) -> str | None:
    """Backticked path: resolved against the document directory, then the repository root, then apps/api. Returns the error text or None."""
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
    return f"repository path missing: `{token}`"


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
    """GitHub heading anchor: lowercase, punctuation removed (letters, digits including CJK, spaces, hyphens and underscores kept), spaces to hyphens."""
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
    """runbook_url in alert rules: the target file must exist and the anchor must match a heading. Returns the error list."""
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
                            findings.append(f"{rel}:{lineno}: runbook_url target missing: {repo_path}")
                            continue
                        if anchor:
                            anchor = urllib.parse.unquote(anchor)
                            if anchor not in heading_slugs(target):
                                findings.append(
                                    f"{rel}:{lineno}: runbook_url anchor missing: #{anchor} (target {repo_path})"
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
        print(f"docs reference check passed ({len(files)} files)")
        return 0
    for line in findings:
        print(f"::error::{line}")
    print(f"{len(findings)} broken reference(s): fix the reference, or update the document when it points at a deleted file")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
