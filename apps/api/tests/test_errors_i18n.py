"""消息参数插值、缺键与缺参数回退,以及中文错误目录导出一致性。"""

from pathlib import Path
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.core.messages import MESSAGES, render_message


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
    import importlib.util

    repo = Path(__file__).resolve().parents[3]
    script = repo / "apps" / "api" / "scripts" / "export_error_messages.py"
    spec = importlib.util.spec_from_file_location("export_error_messages", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "errors.json"
    mod_any: Any = mod
    mod_any.OUT = out
    mod_any.main()
    checked_in = (repo / "packages" / "ui" / "locales" / "zh-CN" / "errors.json").read_text(
        encoding="utf-8"
    )
    assert out.read_text(encoding="utf-8") == checked_in, (
        "core/messages.py 与 errors.json 漂移:跑 export_error_messages.py 并提交"
    )
