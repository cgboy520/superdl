"""错误文案目录:AppError(key=...) 的单一事实源。

- 键命名 `<模块>.<camelCase 语义槽>`,wire 上不带 namespace 前缀(前端固定查 errors ns)。
- 占位符用 str.format 形式 `{name}`;导出脚本转为 i18next 的 `{{name}}`。
- 生成链:scripts/export_error_messages.py 读本表写 packages/ui/locales/zh-CN/errors.json
  (CI no-diff 锁);en 手译,键集与占位符 parity 由 packages/ui 的 locales.test 锁。
"""

from collections.abc import Mapping
from typing import Any

MESSAGES: dict[str, str] = {
    # 通用兜底(errors.py 三个 helper 与 422/500 handler 使用)
    "common.forbidden": "无权访问",
    "common.internal": "服务器内部错误,请稍后重试",
    "common.notFound": "资源不存在",
    "common.unauthorized": "未登录或凭证已过期",
    "common.validation": "参数校验失败",
}


def render_message(key: str, params: Mapping[str, Any] | None) -> str:
    """按目录渲染中文兜底文案。缺键/缺参回落并留痕,绝不因此 500。"""
    from app.core.logging import get_logger

    template = MESSAGES.get(key)
    if template is None:
        get_logger("app.messages").warning("message_key_missing", key=key)
        return key
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        get_logger("app.messages").warning("message_params_mismatch", key=key)
        return template
