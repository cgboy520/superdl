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
    """快照并恢复 setup_logging 改的全局配置。"""
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
    setup_logging()

    structlog.contextvars.bind_contextvars(request_id="rid-bridge-1")
    try:
        get_logger("t.struct").info("evt_struct_side")
        logging.getLogger("t.stdlib").warning("evt_stdlib_side")
    finally:
        structlog.contextvars.unbind_contextvars("request_id")

    out = buf.getvalue()
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


def test_sensitive_fields_masked(restore_logging: None, monkeypatch):
    """phone/id_number/token/secret/password/code 键名命中即打码。"""
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
        unrelated="13800002222",
    )

    out = buf.getvalue()
    assert "138****1111" in out
    assert "13800001111" not in out
    assert "110101199001011234" not in out
    assert "sdln_secret-token" not in out
    assert "123456" not in out
    assert "keep" in out
    assert "13800002222" in out


def test_exception_traceback_never_carries_frame_locals(restore_logging: None, monkeypatch):
    """prod 的结构化栈帧不带局部变量。"""
    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod", raising=False)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    setup_logging()

    def _raise_holding_a_secret() -> None:
        leaky_config = {"database_url": "postgresql://u:hunter2-marker@h/db"}
        assert leaky_config
        raise ValueError("boom-marker")

    try:
        _raise_holding_a_secret()
    except ValueError:
        get_logger("t.exc").exception("evt_with_traceback")

    out = buf.getvalue()
    assert "boom-marker" in out
    assert "_raise_holding_a_secret" in out
    assert "hunter2-marker" not in out
    assert "leaky_config" not in out
