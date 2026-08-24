"""把 core/messages.py 的中文目录导出为前端 errors namespace(zh 基准)。

产物 packages/ui/locales/zh-CN/errors.json 入库,CI 以 no-diff 校验单一事实源不漂移;
en-US/errors.json 手译,键集/占位符一致性由 packages/ui 的 locales.test 锁定。
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许 scripts/ 直跑

from app.core.messages import MESSAGES

OUT = Path(__file__).resolve().parents[3] / "packages" / "ui" / "locales" / "zh-CN" / "errors.json"


def to_i18next(template: str) -> str:
    return re.sub(r"\{(\w+)\}", r"{{\1}}", template)


def main() -> None:
    tree: dict = {}
    for key in sorted(MESSAGES):
        node = tree
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = to_i18next(MESSAGES[key])
    OUT.write_text(
        json.dumps(tree, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sys.stdout.write(f"wrote {OUT}\n")


if __name__ == "__main__":
    main()
