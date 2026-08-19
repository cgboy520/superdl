"""CI 闸门:计费模块(app/modules/billing)行覆盖率必须 ≥90%。

用法:pytest --cov=app --cov-report=json 后运行本脚本。
"""

import json
import sys
from pathlib import Path

THRESHOLD = 90.0


def main() -> int:
    data = json.loads(Path("coverage.json").read_text())
    total_statements = 0
    total_missed = 0
    for path, info in data["files"].items():
        if "app/modules/billing/" not in path.replace("\\", "/"):
            continue
        s = info["summary"]
        total_statements += s["num_statements"]
        total_missed += s["missing_lines"]
    if total_statements == 0:
        print("billing coverage: no files measured")  # noqa: T201
        return 1
    pct = 100.0 * (total_statements - total_missed) / total_statements
    print(f"billing coverage: {pct:.1f}% (threshold {THRESHOLD}%)")  # noqa: T201
    return 0 if pct >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
