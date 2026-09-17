"""Message interpolation, missing-key / missing-param fallbacks, the en-US export lock and the
hand-maintained zh-CN catalog's key and placeholder parity."""

import json
import re
from pathlib import Path
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.core.messages import MESSAGES, render_message

REPO = Path(__file__).resolve().parents[3]
LOCALES = REPO / "packages" / "ui" / "locales"


def test_params_interpolation() -> None:
    MESSAGES["_test.withParam"] = "At most {max} instances"
    try:
        err = AppError(ErrorCode.CONFLICT, key="_test.withParam", params={"max": 10})
        assert err.message == "At most 10 instances"
        assert err.params == {"max": 10}
    finally:
        MESSAGES.pop("_test.withParam")


def test_missing_key_falls_back_to_key_not_500() -> None:
    assert render_message("nope.missing", None) == "nope.missing"


def test_params_mismatch_falls_back_to_template() -> None:
    MESSAGES["_test.bad"] = "needs {x}"
    try:
        assert render_message("_test.bad", None) == "needs {x}"
    finally:
        MESSAGES.pop("_test.bad")


def test_export_script_matches_checked_in_catalog(tmp_path: Path) -> None:
    import importlib.util

    script = REPO / "apps" / "api" / "scripts" / "export_error_messages.py"
    spec = importlib.util.spec_from_file_location("export_error_messages", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "errors.json"
    mod_any: Any = mod
    mod_any.OUT = out
    mod_any.main()
    checked_in = (LOCALES / "en-US" / "errors.json").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == checked_in, (
        "core/messages.py and en-US/errors.json drifted: run export_error_messages.py and commit"
    )


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            out.update(_flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def test_zh_catalog_matches_english_source_keys_and_placeholders() -> None:
    """zh-CN/errors.json is hand-maintained: every key of MESSAGES, no extras, same placeholders."""
    zh = _flatten(json.loads((LOCALES / "zh-CN" / "errors.json").read_text(encoding="utf-8")))
    assert set(zh) == set(MESSAGES), {
        "missing_in_zh": sorted(set(MESSAGES) - set(zh)),
        "extra_in_zh": sorted(set(zh) - set(MESSAGES)),
    }
    for key, template in MESSAGES.items():
        assert zh[key].strip(), f"zh-CN {key} is empty"
        expected = set(re.findall(r"\{(\w+)\}", template))
        actual = set(re.findall(r"\{\{(\w+)\}\}", zh[key]))
        assert actual == expected, f"placeholder mismatch for {key}: {actual} != {expected}"
