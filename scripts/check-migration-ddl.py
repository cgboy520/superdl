#!/usr/bin/env python3
"""alembic 迁移危险 DDL 门禁(CI 调用,stdlib 零依赖)。

只检查传入迁移文件的 upgrade():downgrade() 里的 drop_table/drop_column
是建表迁移的正常回滚,不算危险。squawk 不适用 —— 迁移是 alembic Python
而非纯 SQL,故做 AST 级检查。

命中以下危险操作时,文件内需显式标注 `# ddl-risk: reviewed`(说明为何可接受):
- drop_column / drop_table / rename_table / alter_column(new_column_name=...)
- op.execute 裸 SQL 里 ADD CONSTRAINT 未带 NOT VALID(锁表校验存量行)

用法: python3 scripts/check-migration-ddl.py <迁移文件>...
"""

import ast
import re
import sys

MARKER = "# ddl-risk: reviewed"
ADD_CONSTRAINT_RE = re.compile(r"\bADD\s+CONSTRAINT\b", re.IGNORECASE)
NOT_VALID_RE = re.compile(r"\bNOT\s+VALID\b", re.IGNORECASE)


def _attr_name(node: ast.AST) -> str:
    """把 op.drop_column / sa.text 这类调用目标还原成点分名。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _upgrade_calls(tree: ast.Module) -> list[ast.Call]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    return []


def check_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        source = f.read()
    if MARKER in source:
        return []  # 已显式标注评审,放行
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [f"{path}: 语法解析失败: {exc}"]

    findings: list[str] = []
    for call in _upgrade_calls(tree):
        target = _attr_name(call.func)
        short = target.rsplit(".", 1)[-1]
        if short in ("drop_column", "drop_table", "rename_table"):
            findings.append(f"{path}:{call.lineno}: upgrade() 中的 {short} 属危险 DDL")
        elif short == "alter_column" and any(
            kw.arg == "new_column_name" for kw in call.keywords
        ):
            findings.append(f"{path}:{call.lineno}: alter_column 改列名属危险 DDL")
        elif short == "execute":
            for arg in call.args:
                sql = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ""
                if ADD_CONSTRAINT_RE.search(sql) and not NOT_VALID_RE.search(sql):
                    findings.append(
                        f"{path}:{call.lineno}: ADD CONSTRAINT 未带 NOT VALID(全表校验锁)"
                    )
    return findings


def main(argv: list[str]) -> int:
    findings: list[str] = []
    for path in argv[1:]:
        findings.extend(check_file(path))
    if not findings:
        print("迁移危险 DDL 检查通过")
        return 0
    for f in findings:
        print(f"::error::{f}")
    print(f"危险 DDL 需在迁移文件内显式标注 `{MARKER}` 并说明理由后方可放行")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
