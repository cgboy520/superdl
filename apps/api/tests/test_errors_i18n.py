"""message_key 机制:key 渲染/params 插值/旧签名兼容/缺键回落/422 兜底键/导出脚本幂等。"""

import subprocess
import sys
from pathlib import Path

from app.core.errors import AppError, ErrorCode, not_found
from app.core.messages import MESSAGES, render_message


def test_key_renders_chinese_message() -> None:
    err = not_found()
    assert err.message == "资源不存在"
    assert err.message_key == "common.notFound"


def test_legacy_message_signature_unchanged() -> None:
    err = AppError(ErrorCode.CONFLICT, "自定义中文")
    assert err.message == "自定义中文"
    assert err.message_key is None
    assert err.params is None


def test_params_interpolation() -> None:
    MESSAGES["_test.withParam"] = "上限 {max} 台"
    try:
        err = AppError(ErrorCode.CONFLICT, key="_test.withParam", params={"max": 10})
        assert err.message == "上限 10 台"
        assert err.params == {"max": 10}
    finally:
        MESSAGES.pop("_test.withParam")


def test_missing_key_falls_back_to_key_not_500() -> None:
    assert render_message("nope.missing", None) == "nope.missing"


def test_params_mismatch_falls_back_to_template() -> None:
    MESSAGES["_test.bad"] = "需要 {x}"
    try:
        assert render_message("_test.bad", None) == "需要 {x}"
    finally:
        MESSAGES.pop("_test.bad")


def test_export_script_matches_checked_in_catalog(tmp_path: Path) -> None:
    """生成链 no-diff:入库的 zh errors.json 必须与 MESSAGES 同步(CI 同款校验)。"""
    repo = Path(__file__).resolve().parents[3]
    target = repo / "packages" / "ui" / "locales" / "zh-CN" / "errors.json"
    before = target.read_text()
    subprocess.run(
        [sys.executable, str(repo / "apps" / "api" / "scripts" / "export_error_messages.py")],
        check=True,
    )
    after = target.read_text()
    assert after == before, (
        "core/messages.py 与 errors.json 漂移:跑 export_error_messages.py 并提交"
    )
