"""日志桥接:stdlib 第三方日志与 structlog 同一渲染管道,contextvars 不丢;
SUPERDL_LOG_LEVEL 同时约束两侧。"""

import io
import logging
import sys
from collections.abc import Iterator

import pytest
import structlog

from app.core.logging import get_logger, setup_logging


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """setup_logging 改全局(root handler/structlog 配置):快照恢复,防顺序相关污染。"""
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    old_cfg = structlog.get_config()
    yield
    root.handlers[:] = old_handlers
    root.setLevel(old_level)
    structlog.configure(**old_cfg)


def test_stdlib_logs_enter_structlog_pipeline(restore_logging: None, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    setup_logging()  # environment=test → ConsoleRenderer(prod 为 JSON,同一管道)

    structlog.contextvars.bind_contextvars(request_id="rid-bridge-1")
    try:
        get_logger("t.struct").info("evt_struct_side")
        logging.getLogger("t.stdlib").warning("evt_stdlib_side")  # 第三方库形态
    finally:
        structlog.contextvars.unbind_contextvars("request_id")

    out = buf.getvalue()
    # 两侧事件都进同一输出;stdlib 侧同样合并 contextvars(request_id 串联)
    assert "evt_struct_side" in out
    assert "evt_stdlib_side" in out
    assert "rid-bridge-1" in out


def test_log_level_config_filters_both_sides(restore_logging: None, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SUPERDL_LOG_LEVEL", "ERROR")
    get_settings.cache_clear()
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    try:
        setup_logging()
        get_logger("t.struct").info("info_struct_hidden")
        get_logger("t.struct").error("error_struct_shown")
        logging.getLogger("t.stdlib").warning("warn_stdlib_hidden")
        logging.getLogger("t.stdlib").error("error_stdlib_shown")
    finally:
        get_settings.cache_clear()

    out = buf.getvalue()
    assert "info_struct_hidden" not in out
    assert "warn_stdlib_hidden" not in out
    assert "error_struct_shown" in out
    assert "error_stdlib_shown" in out


def test_exception_traceback_rendered(restore_logging: None, monkeypatch):
    """prod(JSON):logger.exception 渲染成结构化栈帧(dict_tracebacks),不再只剩一行 event。
    dev 的 ConsoleRenderer 自己渲染 exc_info(不能叠 dict_tracebacks,见其 TypeError)。"""
    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod", raising=False)  # 绕开 prod 全量校验
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    setup_logging()

    try:
        raise ValueError("boom-marker")
    except ValueError:
        get_logger("t.exc").exception("evt_with_traceback")

    out = buf.getvalue()
    assert "evt_with_traceback" in out
    assert "boom-marker" in out  # 异常消息/栈帧进入渲染输出
    assert "ValueError" in out


def test_sensitive_fields_masked(restore_logging: None, monkeypatch):
    """PII/凭据全局兜底(P1-13):phone/id_number/token/secret/password/code 键名命中即打码
    (挂了 = 新增日志点忘脱敏,手机号/凭据明文进 Loki 180 天)。"""
    from app.core.config import get_settings

    get_settings.cache_clear()
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    setup_logging()

    get_logger("t.mask").info(
        "evt_mask",
        phone="13800001111",
        id_number="110101199001011234",
        token="sdln_secret-token",
        params={"code": "123456", "note": "keep"},
        unrelated="13800002222",  # 键名不命中:不打码(防误伤业务值)
    )

    out = buf.getvalue()
    assert "138****1111" in out
    assert "13800001111" not in out
    assert "110101199001011234" not in out
    assert "sdln_secret-token" not in out
    assert "123456" not in out  # 嵌套 dict 的 code 键同样打码
    assert "keep" in out
    assert "13800002222" in out  # 键名不命中原样保留
