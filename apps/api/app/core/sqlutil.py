"""SQL LIKE/ILIKE 元字符转义(单一定义点)。与 SQLAlchemy 的 escape="\\" 配套。"""


def like_escape(q: str) -> str:
    """转义 LIKE 元字符(\\ % _):调用方拼 f"%{like_escape(q)}%" 并传 escape="\\"。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
