"""导出 openapi.json 到 packages/api-client/(orval 的输入)。CI 校验其与代码一致。"""

import json
from pathlib import Path

from app.main import create_app


def main() -> None:
    app = create_app()
    spec = app.openapi()
    out = Path(__file__).resolve().parents[3] / "packages" / "api-client" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"openapi.json written: {out}")  # noqa: T201


if __name__ == "__main__":
    main()
