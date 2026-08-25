#!/usr/bin/env python3
"""alembic 迁移危险 DDL 门禁(CI 调用,stdlib 零依赖)。

只检查传入迁移文件的 upgrade():downgrade() 里的 drop_table/drop_column
是建表迁移的正常回滚,不算危险。squawk 不适用 —— 迁移是 alembic Python
而非纯 SQL,故做 AST 级检查。

命中以下危险操作时,文件内需显式标注 `# ddl-risk: reviewed`(说明为何可接受):
- drop_column / drop_table / rename_table / alter_column(new_column_name=...)
- op.execute 裸 SQL 里 ADD CONSTRAINT 未带 NOT VALID(锁表校验存量行)
- add_column(nullable=False) 且无 server_default(大表重写/全表校验;三步法见
  deploy/README.md「迁移向前兼容窗口(expand-only)规范」)
- create_index 未带 postgresql_concurrently=True(在线建索引锁写;
  本迁移内 create_table 新建表上的索引豁免——空表建索引零成本)
- alter_column(type_=...) 或裸 SQL ALTER ... TYPE(列类型变更重写整表;
  字符串同族长度调整豁免——varchar 扩长是零重写元数据操作)
- create_index(postgresql_concurrently=True) 或裸 SQL 带 CONCURRENTLY 却不在
  `with op.get_context().autocommit_block():` 块内(alembic/env.py 整轮单事务,
  CREATE INDEX CONCURRENTLY 在事务块内直接报错,升级会卡死在这一步)

用法: python3 scripts/check-migration-ddl.py <迁移文件>...
"""

import ast
import re
import sys

MARKER = "# ddl-risk: reviewed"
ADD_CONSTRAINT_RE = re.compile(r"\bADD\s+CONSTRAINT\b", re.IGNORECASE)
NOT_VALID_RE = re.compile(r"\bNOT\s+VALID\b", re.IGNORECASE)
ALTER_TYPE_RE = re.compile(r"\bALTER\s+(TYPE\b|TABLE\b[^;]*\bTYPE\b)", re.IGNORECASE)
CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)


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


def _autocommit_call_ids(tree: ast.Module) -> set[int]:
    """upgrade() 里 `with op.get_context().autocommit_block():` 块内全部 Call 节点的 id。"""
    ids: set[int] = set()
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == "upgrade"):
            continue
        for w in ast.walk(node):
            if isinstance(w, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and _attr_name(item.context_expr.func).endswith("autocommit_block")
                for item in w.items
            ):
                ids.update(id(c) for c in ast.walk(w) if isinstance(c, ast.Call))
    return ids


def _has_concurrently(call: ast.Call) -> bool:
    return any(
        kw.arg == "postgresql_concurrently"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in call.keywords
    )


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
    calls = _upgrade_calls(tree)
    autocommit_ids = _autocommit_call_ids(tree)
    # 本迁移内新建的表:create_table 之后的 create_index 是空表建索引(零成本),
    # 不算「在线建索引锁写」;只对既有表上的索引要求 CONCURRENTLY。
    new_tables = {
        c.args[0].value
        for c in calls
        if _attr_name(c.func).endswith("create_table")
        and c.args
        and isinstance(c.args[0], ast.Constant)
        and isinstance(c.args[0].value, str)
    }
    for call in calls:
        target = _attr_name(call.func)
        short = target.rsplit(".", 1)[-1]
        if short in ("drop_column", "drop_table", "rename_table"):
            findings.append(f"{path}:{call.lineno}: upgrade() 中的 {short} 属危险 DDL")
        elif short == "alter_column" and any(
            kw.arg == "new_column_name" for kw in call.keywords
        ):
            findings.append(f"{path}:{call.lineno}: alter_column 改列名属危险 DDL")
        elif short == "alter_column" and _is_type_change(call):
            findings.append(
                f"{path}:{call.lineno}: alter_column(type_=...) 列类型变更重写整表,拆窗口进行"
            )
        elif short == "add_column" and _is_not_null_without_default(call):
            findings.append(
                f"{path}:{call.lineno}: add_column(nullable=False) 无 server_default"
                "(大表按三步法:可空加列 → 回填 → 校验收口,见 deploy/README.md)"
            )
        elif (
            short == "create_index"
            and not _index_on_new_table(call, new_tables)
            and not _has_concurrently(call)
        ):
            findings.append(
                f"{path}:{call.lineno}: create_index 未带 postgresql_concurrently=True(在线建索引锁写)"
            )
        elif short == "create_index" and _has_concurrently(call) and id(call) not in autocommit_ids:
            findings.append(
                f"{path}:{call.lineno}: create_index(postgresql_concurrently=True) 必须放在"
                " `with op.get_context().autocommit_block():` 内(env.py 整轮单事务,"
                "CONCURRENTLY 在事务块内直接报错)"
            )
        elif short == "execute":
            for arg in call.args:
                sql = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ""
                if ADD_CONSTRAINT_RE.search(sql) and not NOT_VALID_RE.search(sql):
                    findings.append(
                        f"{path}:{call.lineno}: ADD CONSTRAINT 未带 NOT VALID(全表校验锁)"
                    )
                if ALTER_TYPE_RE.search(sql):
                    findings.append(
                        f"{path}:{call.lineno}: 裸 SQL ALTER TYPE/ALTER ... TYPE 列类型变更,拆窗口进行"
                    )
                if CONCURRENTLY_RE.search(sql) and id(call) not in autocommit_ids:
                    findings.append(
                        f"{path}:{call.lineno}: 裸 SQL CONCURRENTLY 必须放在"
                        " `with op.get_context().autocommit_block():` 内(事务块内直接报错)"
                    )
    return findings


def _index_on_new_table(call: ast.Call, new_tables: set[str]) -> bool:
    """create_index 的表名实参(op.create_index(<name>, <table>, ...))是否本迁移新建。"""
    return (
        len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value in new_tables
    )


# 字符串同族类型调整(varchar 扩长在 PG 是零重写元数据操作;缩长属 contract 窗口,
# 由 review 把关),不算「重写整表」的类型变更
_STRING_TYPES = ("String", "VARCHAR", "Unicode", "UnicodeText", "Text")


def _is_type_change(call: ast.Call) -> bool:
    """alter_column 是否带重写型 type_=... 变更(字符串同族调整豁免,见上方注释)。"""
    for kw in call.keywords:
        if kw.arg != "type_":
            continue
        if isinstance(kw.value, ast.Call) and _attr_name(kw.value.func).endswith(_STRING_TYPES):
            return False
        return True
    return False


def _is_not_null_without_default(call: ast.Call) -> bool:
    """add_column 的 Column 实参是否 nullable=False 且无 server_default。

    只识别字面写法(sa.Column(..., nullable=False));server_default 给任何值
    (含 None)都算「有默认」——PG ≥ 11 加带默认列是 O(1) 元数据操作。
    """
    # op.add_column(table_name, column, ...):Column 实参从 args[1] 起
    for arg in call.args[1:]:
        if not (isinstance(arg, ast.Call) and _attr_name(arg.func).endswith("Column")):
            continue
        not_null = any(
            kw.arg == "nullable" and isinstance(kw.value, ast.Constant) and kw.value.value is False
            for kw in arg.keywords
        )
        has_default = any(kw.arg == "server_default" for kw in arg.keywords)
        if not_null and not has_default:
            return True
    return False


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
