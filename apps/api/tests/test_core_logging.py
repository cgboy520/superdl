"""日志桥接(P1-32b):stdlib 第三方日志与 structlog 同一渲染管道,contextvars 不丢;
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
